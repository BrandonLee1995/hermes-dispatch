"""Regression checks for the persistent Codex app-server phase hotfix."""

from __future__ import annotations

import base64
import importlib.util
import json
import math
import os
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace


def load_plugin_module():
    # Exercise the checkout under test.  The /opt/data fallback keeps this
    # script usable when it is copied into a minimal container by itself.
    path = Path(__file__).with_name("__init__.py")
    if not path.exists():
        path = Path("/opt/data/plugins/codex-app-server-phase-hotfix/__init__.py")
    spec = importlib.util.spec_from_file_location(
        "codex_app_server_phase_hotfix_test",
        path,
        submodule_search_locations=[str(path.parent)],
    )
    module = importlib.util.module_from_spec(spec)
    module.__package__ = spec.name
    module.__path__ = [str(path.parent)]
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mod = load_plugin_module()


def completed(text: str, phase=None):
    item = {"type": "agentMessage", "id": text, "text": text}
    if phase is not None:
        item["phase"] = phase
    return {"method": "item/completed", "params": {"item": item}}


def main():
    # Long turns: 0 means no Codex wall-clock deadline. The wrapper keeps all
    # completion and callback state on the individual session/call.
    assert math.isinf(mod.long_turn.configured_turn_timeout("0"))
    assert math.isinf(mod.long_turn.configured_turn_timeout("invalid"))
    assert mod.long_turn.configured_turn_timeout("7200") == 7200

    class TurnResult:
        def __init__(self, text):
            self.final_text = text
            self.turn_id = f"turn-{text}"
            self.interrupted = False
            self.error = None
            self.should_retire = False

    concurrent_barrier = threading.Barrier(2)

    def isolated_original(
        session,
        user_input,
        *,
        turn_timeout,
        notification_poll_timeout,
        post_tool_quiet_timeout,
    ):
        session.received_timeout = turn_timeout
        concurrent_barrier.wait(timeout=2)
        session._on_event(
            {
                "method": "turn/completed",
                "params": {"turn": {"id": f"turn-{user_input}"}},
            }
        )
        return TurnResult(user_input)

    isolated_wrapper = mod.long_turn.wrap_session_run_turn(isolated_original)

    class IsolatedSession:
        def __init__(self, name):
            self.name = name
            self.events = []
            self._on_event = self.events.append
            self.interrupts = []

        def _issue_interrupt(self, turn_id):
            self.interrupts.append(turn_id)

    sessions = [IsolatedSession("alpha"), IsolatedSession("beta")]
    results = {}

    def exercise(session, timeout):
        results[session.name] = isolated_wrapper(
            session,
            session.name,
            turn_timeout=timeout,
        )

    threads = [
        threading.Thread(target=exercise, args=(sessions[0], 31)),
        threading.Thread(target=exercise, args=(sessions[1], 47)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)
        assert not thread.is_alive()
    assert sessions[0].received_timeout == 31
    assert sessions[1].received_timeout == 47
    assert results["alpha"].final_text == "alpha"
    assert results["beta"].final_text == "beta"
    assert "alpha" not in str(sessions[1].events)
    assert "beta" not in str(sessions[0].events)
    assert not sessions[0].interrupts and not sessions[1].interrupts

    # If an operator deliberately restores a finite timeout, an assistant
    # commentary item at the deadline must not be accepted as final success.
    def deadline_original(
        session,
        user_input,
        *,
        turn_timeout,
        notification_poll_timeout,
        post_tool_quiet_timeout,
    ):
        time.sleep(turn_timeout)
        return TurnResult("still working")

    import time

    deadline_session = IsolatedSession("deadline")
    deadline_result = mod.long_turn.wrap_session_run_turn(deadline_original)(
        deadline_session,
        "work",
        turn_timeout=0.03,
        notification_poll_timeout=0.001,
    )
    assert deadline_result.interrupted is True
    assert deadline_result.should_retire is True
    assert "without turn/completed" in deadline_result.error
    assert deadline_session.interrupts == ["turn-still working"]

    delegated = []

    def original_factory(_agent):
        return delegated.append

    wrapped = mod.phase_filter.wrap_event_bridge_factory(original_factory)
    bridge = wrapped(object())

    bridge(completed("working", "commentary"))
    assert [n["params"]["item"]["text"] for n in delegated] == ["working"]

    bridge(completed("done", "final_answer"))
    assert [n["params"]["item"]["text"] for n in delegated] == ["working"]

    bridge(completed("legacy-final"))
    bridge({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})
    assert "legacy-final" not in str(delegated)

    bridge(completed("legacy-commentary"))
    bridge(
        {
            "method": "item/started",
            "params": {"item": {"type": "commandExecution", "id": "cmd-1"}},
        }
    )
    assert any(
        n.get("params", {}).get("item", {}).get("text") == "legacy-commentary"
        for n in delegated
    )

    status = mod.phase_filter.patch_codex_app_server_event_bridge()
    status_again = mod.phase_filter.patch_codex_app_server_event_bridge()
    assert status in {
        "patched Codex agentMessage phase routing",
        "upstream already filters final_answer; skipped",
    }
    assert status_again in {
        "already patched",
        "upstream already filters final_answer; skipped",
    }

    old_home = os.environ.get("HERMES_HOME")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["HERMES_HOME"] = tmp
        image_status = mod.image_delivery.patch_codex_image_delivery()
        image_status_again = mod.image_delivery.patch_codex_image_delivery()

        from agent.transports.codex_event_projector import CodexEventProjector

        tiny_png = base64.b64encode(
            b"\x89PNG\r\n\x1a\n" + b"codex-image-test"
        ).decode("ascii")
        projection = CodexEventProjector().project(
            {
                "method": "item/completed",
                "params": {
                    "item": {
                        "type": "imageGeneration",
                        "id": "exec-image-test",
                        "status": "completed",
                        "revisedPrompt": "test image",
                        "result": tiny_png,
                    }
                },
            }
        )
        assert len(projection.messages) == 2
        tool_call = projection.messages[0]["tool_calls"][0]
        assert tool_call["function"]["name"] == "image_generate"
        tool_payload = json.loads(projection.messages[1]["content"])
        image_path = Path(tool_payload["image"])
        assert tool_payload["success"] is True
        assert image_path.is_file()
        assert image_path.parent == (Path(tmp) / "cache" / "images").resolve()

        def empty_image_turn(_agent, **kwargs):
            kwargs["messages"].extend(projection.messages)
            return {
                "final_response": "",
                "messages": kwargs["messages"],
                "api_calls": 1,
                "completed": True,
            }

        wrapped_turn = mod.image_delivery.wrap_codex_runtime_turn(empty_image_turn)
        history = [{"role": "user", "content": "generate"}]
        recovered = wrapped_turn(object(), messages=history)
        assert recovered["final_response"] == f"MEDIA:{image_path.resolve()}"

        assert "projector patched" in image_status
        assert "already patched" in image_status_again

    # Current Codex app-server permissions protocol: only the granted subset
    # is returned. An empty profile is a denial; persistence is session-only.
    requested = {
        "network": {"enabled": True, "ignored": "not-on-wire"},
        "fileSystem": {
            "write": ["/tmp/export"],
            "entries": [
                {
                    "access": "write",
                    "path": {"type": "path", "path": "/tmp/export"},
                }
            ],
            "ignored": ["/etc"],
        },
        "unknown": {"enabled": True},
    }
    once_response = mod.approval_bridge.permission_response(requested, "once")
    assert once_response["scope"] == "turn"
    assert once_response["permissions"]["network"] == {"enabled": True}
    assert once_response["permissions"]["fileSystem"]["write"] == ["/tmp/export"]
    assert "ignored" not in once_response["permissions"]["fileSystem"]
    assert "unknown" not in once_response["permissions"]
    assert mod.approval_bridge.permission_response(requested, "always") == {
        "permissions": once_response["permissions"],
        "scope": "session",
    }
    assert mod.approval_bridge.permission_response(requested, "deny") == {
        "permissions": {},
        "scope": "turn",
    }

    permission_responses = []

    class PermissionClient:
        def respond(self, request_id, result):
            permission_responses.append((request_id, result))

    permission_callback_calls = []

    class PermissionSession:
        _client = PermissionClient()
        _approval_callback = staticmethod(
            lambda *args, **kwargs: permission_callback_calls.append((args, kwargs))
            or "once"
        )

    delegated_requests = []
    permission_handler = mod.approval_bridge.wrap_server_request_handler(
        lambda _self, request: delegated_requests.append(request)
    )
    permission_handler(
        PermissionSession(),
        {
            "id": 61,
            "method": "item/permissions/requestApproval",
            "params": {
                "cwd": "/workspace",
                "reason": "download a dependency",
                "permissions": {"network": {"enabled": True}},
            },
        },
    )
    assert permission_responses == [
        (61, {"permissions": {"network": {"enabled": True}}, "scope": "turn"})
    ]
    assert permission_callback_calls[0][1]["allow_permanent"] is False
    assert permission_callback_calls[0][1]["approval_choices"] == [
        "once",
        "session",
        "deny",
    ]
    assert delegated_requests == []

    execpolicy_params = {
        "command": "curl https://example.invalid",
        "cwd": "/workspace",
        "proposedExecpolicyAmendment": ["prefix_rule", "curl"],
    }
    assert mod.approval_bridge.command_approval_choices(execpolicy_params) == [
        "once",
        "session",
        "always",
        "deny",
    ]
    assert mod.approval_bridge.command_response(execpolicy_params, "session") == {
        "decision": "acceptForSession"
    }
    assert mod.approval_bridge.command_response(execpolicy_params, "always") == {
        "decision": {
            "acceptWithExecpolicyAmendment": {
                "execpolicy_amendment": ["prefix_rule", "curl"]
            }
        }
    }
    assert mod.approval_bridge.command_response(
        {
            "proposedNetworkPolicyAmendments": [
                {"host": "example.invalid", "action": "allow"}
            ]
        },
        "always",
    ) == {
        "decision": {
            "applyNetworkPolicyAmendment": {
                "network_policy_amendment": {
                    "host": "example.invalid",
                    "action": "allow",
                }
            }
        }
    }
    assert mod.approval_bridge.file_change_response("session") == {
        "decision": "acceptForSession"
    }

    # Computer Use asks through MCP elicitation rather than the standalone
    # permissions method. Only the bundled connector identity is intercepted.
    computer_use_request = {
        "id": 63,
        "method": "mcpServer/elicitation/request",
        "params": {
            "serverName": "node-repl",
            "threadId": "thread-1",
            "turnId": "turn-1",
            "mode": "openai/form",
            "message": 'Allow Computer Use to use "Notes"?',
            "requestedSchema": {},
            "_meta": {
                "connector_id": "computer-use",
                "connector_name": "Computer Use",
                "persist": ["session", "always"],
                "riskLevel": "medium",
                "tool_params": {"app": "com.apple.Notes"},
                "tool_params_display": [
                    {"name": "app", "display_name": "App", "value": "Notes"}
                ],
            },
        },
    }
    permission_handler(PermissionSession(), computer_use_request)
    assert permission_responses[-1] == (
        63,
        {"action": "accept", "content": None, "_meta": None},
    )
    cua_args, cua_kwargs = permission_callback_calls[-1]
    assert cua_args[0] == "computer_use app=com.apple.Notes"
    assert 'Allow Computer Use to use "Notes"?' in cua_args[1]
    assert cua_kwargs["allow_permanent"] is True
    assert cua_kwargs["approval_choices"] == [
        "once",
        "session",
        "always",
        "deny",
    ]

    assert mod.approval_bridge.computer_use_elicitation_response("always") == {
        "action": "accept",
        "content": None,
        "_meta": {"persist": "always"},
    }
    assert mod.approval_bridge.computer_use_elicitation_response("deny") == {
        "action": "decline",
        "content": None,
        "_meta": None,
    }

    unrelated_elicitation = {
        "id": 64,
        "method": "mcpServer/elicitation/request",
        "params": {
            "serverName": "third-party-mcp",
            "threadId": "thread-1",
            "mode": "form",
            "message": "Provide a value",
            "requestedSchema": {"type": "object", "properties": {}},
        },
    }
    permission_handler(PermissionSession(), unrelated_elicitation)
    assert delegated_requests[-1] == unrelated_elicitation

    # Exercise the same private queue contract Hermes' own command guards use.
    notified = []
    approval_module = SimpleNamespace(
        _lock=threading.Lock(),
        _gateway_notify_cbs={"qq-session": notified.append},
        get_current_session_key=lambda default="": "qq-session",
        _await_gateway_decision=lambda session_key, notify, data, surface: (
            notify(data) or {"resolved": True, "choice": "once"}
        ),
    )
    gateway_choice = mod.approval_bridge._gateway_approval_choice(
        "curl https://example.invalid/file",
        "Codex requests network access",
        approval_choices=["once", "session", "deny"],
        _approval_module=approval_module,
    )
    assert gateway_choice == "once"
    assert notified and notified[0]["allow_permanent"] is False
    assert notified[0]["codex_approval_choices"] == ["once", "session", "deny"]

    # Integrate with the installed Hermes queue, not only the isolated fake.
    from tools import approval as real_approval

    real_session_key = "codex-approval-hotfix-regression"
    real_notified = []
    real_token = real_approval.set_current_session_key(real_session_key)

    def resolve_real_queue(data):
        real_notified.append(data)
        assert real_approval.resolve_gateway_approval(real_session_key, "once") == 1

    real_approval.register_gateway_notify(real_session_key, resolve_real_queue)
    try:
        assert mod.approval_bridge._gateway_approval_choice(
            "request_permissions network",
            "Codex requests network access",
            approval_choices=["once", "session", "deny"],
        ) == "once"
        assert real_notified[0]["pattern_key"] == "codex_app_server:approval"
        assert real_notified[0]["codex_approval_choices"] == [
            "once",
            "session",
            "deny",
        ]
    finally:
        real_approval.unregister_gateway_notify(real_session_key)
        real_approval.reset_current_session_key(real_token)

    injected_callbacks = []

    def capture_init(_self, **kwargs):
        injected_callbacks.append(kwargs.get("approval_callback"))

    mod.approval_bridge.wrap_session_init(capture_init)(object(), approval_callback=None)
    assert injected_callbacks == [mod.approval_bridge._gateway_approval_choice]

    approval_status = mod.approval_bridge.patch_codex_gateway_approvals()
    approval_status_again = mod.approval_bridge.patch_codex_gateway_approvals()
    assert "gateway callback patched" in approval_status
    assert "command/file approval choices patched" in approval_status
    assert "permission requests patched" in approval_status
    assert "Computer Use elicitations patched" in approval_status
    assert "already patched" in approval_status_again

    long_turn_status = mod.long_turn.patch_codex_app_server_turn_timeout()
    long_turn_status_again = mod.long_turn.patch_codex_app_server_turn_timeout()
    assert "patched Codex turn wall deadline" in long_turn_status
    assert long_turn_status_again == "already patched"

    # Verify all four live session request methods after the class patch.
    from agent.transports.codex_app_server_session import (
        CodexAppServerSession,
        _ServerRequestRouting,
    )

    bridged_responses = []

    class BridgedClient:
        def respond(self, request_id, result):
            bridged_responses.append((request_id, result))

    live_session = object.__new__(CodexAppServerSession)
    live_session._client = BridgedClient()
    live_session._routing = _ServerRequestRouting()
    live_session._cwd = "/workspace"
    live_session._pending_file_changes = {"file-1": "change README.md"}
    live_session._approval_callback = lambda *_args, **_kwargs: "once"
    live_session._handle_server_request(
        {
            "id": 71,
            "method": "item/commandExecution/requestApproval",
            "params": {"command": "curl https://example.invalid", "cwd": "/workspace"},
        }
    )
    live_session._handle_server_request(
        {
            "id": 72,
            "method": "item/fileChange/requestApproval",
            "params": {"itemId": "file-1", "reason": "update documentation"},
        }
    )
    live_session._handle_server_request(
        {
            "id": 73,
            "method": "item/permissions/requestApproval",
            "params": {
                "cwd": "/workspace",
                "permissions": {"network": {"enabled": True}},
            },
        }
    )
    live_session._handle_server_request(
        {
            "id": 74,
            "method": "mcpServer/elicitation/request",
            "params": computer_use_request["params"],
        }
    )
    assert bridged_responses == [
        (71, {"decision": "accept"}),
        (72, {"decision": "accept"}),
        (73, {"permissions": {"network": {"enabled": True}}, "scope": "turn"}),
        (74, {"action": "accept", "content": None, "_meta": None}),
    ]

    scoped_responses = []
    live_session._client = SimpleNamespace(
        respond=lambda request_id, result: scoped_responses.append(
            (request_id, result)
        )
    )
    live_session._approval_callback = lambda *_args, **_kwargs: "session"
    live_session._handle_server_request(
        {
            "id": 75,
            "method": "item/commandExecution/requestApproval",
            "params": {"command": "pwd", "cwd": "/workspace"},
        }
    )
    live_session._handle_server_request(
        {
            "id": 76,
            "method": "item/fileChange/requestApproval",
            "params": {"itemId": "file-1"},
        }
    )
    assert scoped_responses == [
        (75, {"decision": "acceptForSession"}),
        (76, {"decision": "acceptForSession"}),
    ]

    if old_home is None:
        os.environ.pop("HERMES_HOME", None)
    else:
        os.environ["HERMES_HOME"] = old_home

    print("commentary_forwarded=true")
    print("final_answer_suppressed=true")
    print("unknown_terminal_suppressed=true")
    print("image_generation_materialized=true")
    print("empty_image_final_recovered=true")
    print("gateway_approval_callback=true")
    print("gateway_real_queue_roundtrip=true")
    print("complete_command_file_choices=true")
    print("persistent_command_amendment=true")
    print("permission_subset_response=true")
    print("permission_deny_empty_subset=true")
    print("computer_use_elicitation_bridge=true")
    print("unrelated_elicitation_delegated=true")
    print("long_turn_unlimited=true")
    print("long_turn_deadline_terminal_guard=true")
    print("long_turn_session_isolation=true")
    print(f"install_status={status}")


if __name__ == "__main__":
    main()
