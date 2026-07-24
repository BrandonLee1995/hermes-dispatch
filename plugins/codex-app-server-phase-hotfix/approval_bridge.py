"""Bridge Codex app-server approvals into Hermes' gateway approval queue.

Hermes 0.18.2 wires Codex approvals only to the interactive terminal callback.
Gateway sessions therefore fail closed before Discord, QQ, or another adapter
can present the existing approval UI.  Standalone ``request_permissions``
requests are also answered with the legacy ``decision`` response even though
current Codex expects the granted permission subset in ``permissions``.
Computer Use app authorization arrives through ``mcpServer/elicitation/request``;
Hermes declines every non-``hermes-tools`` elicitation, so the Computer Use
runtime receives a denial without a Gateway prompt.

This compatibility patch reuses Hermes' existing per-session blocking queue.
It does not persist grants itself: Codex owns turn/session permission scope,
and an empty granted profile represents an explicit deny.
"""

from __future__ import annotations

import copy
import functools
import json
import logging
from types import SimpleNamespace
from typing import Any, Callable

logger = logging.getLogger(__name__)

_INIT_MARKER = "_codex_gateway_approval_init_hotfix_wrapped"
_REQUEST_MARKER = "_codex_gateway_server_request_hotfix_wrapped"
_COMMAND_METHOD = "item/commandExecution/requestApproval"
_FILE_METHOD = "item/fileChange/requestApproval"
_PERMISSION_METHOD = "item/permissions/requestApproval"
_ELICITATION_METHOD = "mcpServer/elicitation/request"
_COMPUTER_USE_CONNECTOR_ID = "computer-use"
_VALID_CHOICES = {"once", "session", "always", "deny"}
_UI_CHOICES_KEY = "codex_approval_choices"


def _normalize_ui_choices(value: Any) -> list[str]:
    """Return unique Gateway choices in their safe display order."""
    if not isinstance(value, (list, tuple)):
        return []
    requested = {str(item) for item in value if str(item) in _VALID_CHOICES}
    return [choice for choice in ("once", "session", "always", "deny") if choice in requested]


def _gateway_approval_choice(
    command: str,
    description: str,
    *,
    allow_permanent: bool = False,
    approval_choices: Any = None,
    _approval_module=None,
) -> str:
    """Ask the active Hermes gateway session and return its normalized choice.

    The gateway registration API intentionally exposes notification setup but
    not a public "always prompt" entry point.  Hermes' own command guard uses
    the queue helpers below, so this removable compatibility layer follows the
    same locked lookup and blocking wait.  Any missing/changed internal fails
    closed.
    """
    if _approval_module is None:
        try:
            from tools import approval as _approval_module
        except Exception:
            logger.exception("Codex approval bridge could not import tools.approval")
            return "deny"

    try:
        session_key = _approval_module.get_current_session_key(default="")
        if not session_key:
            return "deny"
        with _approval_module._lock:
            notify_cb = _approval_module._gateway_notify_cbs.get(session_key)
        if notify_cb is None:
            logger.debug(
                "Codex approval bridge has no gateway notifier for session %s",
                session_key,
            )
            return "deny"

        try:
            from agent.redact import redact_sensitive_text
        except Exception:
            redact_sensitive_text = str

        approval_data = {
            "command": redact_sensitive_text(str(command or "Codex request")),
            "description": redact_sensitive_text(str(description or "Codex approval")),
            "pattern_key": "codex_app_server:approval",
            "pattern_keys": ["codex_app_server:approval"],
            "allow_permanent": bool(allow_permanent),
        }
        choices = _normalize_ui_choices(approval_choices)
        if choices:
            approval_data[_UI_CHOICES_KEY] = choices
        result = _approval_module._await_gateway_decision(
            session_key,
            notify_cb,
            approval_data,
            surface="codex_app_server",
        )
        choice = result.get("choice") if isinstance(result, dict) else None
        if not result or not result.get("resolved") or choice not in _VALID_CHOICES:
            return "deny"
        return choice
    except Exception:
        logger.exception("Codex gateway approval bridge failed closed")
        return "deny"


def wrap_session_init(original: Callable) -> Callable:
    """Supply the gateway callback when Hermes did not install a CLI callback."""

    @functools.wraps(original)
    def session_init(self, *args, **kwargs):
        if kwargs.get("approval_callback") is None:
            kwargs["approval_callback"] = _gateway_approval_choice
        return original(self, *args, **kwargs)

    setattr(session_init, _INIT_MARKER, True)
    return session_init


def _sanitize_requested_permissions(value: Any) -> dict[str, Any]:
    """Return only fields allowed by Codex's GrantedPermissionProfile schema."""
    if not isinstance(value, dict):
        return {}

    granted: dict[str, Any] = {}
    file_system = value.get("fileSystem")
    if isinstance(file_system, dict):
        allowed_file_system = {
            key: copy.deepcopy(file_system[key])
            for key in ("entries", "globScanMaxDepth", "read", "write")
            if key in file_system
        }
        granted["fileSystem"] = allowed_file_system

    network = value.get("network")
    if isinstance(network, dict):
        allowed_network = {}
        enabled = network.get("enabled")
        if enabled is None or isinstance(enabled, bool):
            allowed_network["enabled"] = enabled
        granted["network"] = allowed_network
    return granted


def permission_response(requested: Any, choice: str) -> dict[str, Any]:
    """Build a current Codex permission response from a Hermes choice.

    Codex treats omitted permissions as denied.  ``always`` is deliberately
    mapped to Codex's session scope; this plugin never changes config.toml or a
    persistent Hermes allowlist.
    """
    if choice not in {"once", "session", "always"}:
        return {"permissions": {}, "scope": "turn"}
    return {
        "permissions": _sanitize_requested_permissions(requested),
        "scope": "session" if choice in {"session", "always"} else "turn",
    }


def _available_decision_kinds(params: dict[str, Any]) -> set[str]:
    """Normalize Codex's optional availableDecisions payload.

    Codex 0.145 can describe simple decisions as strings and amendment-bearing
    decisions as one-key objects.  Unknown future decisions are ignored.
    """
    available = params.get("availableDecisions")
    if not isinstance(available, list):
        return set()
    kinds: set[str] = set()
    for decision in available:
        if isinstance(decision, str):
            kinds.add(decision)
        elif isinstance(decision, dict):
            kinds.update(str(key) for key in decision)
    return kinds


def command_approval_choices(params: dict[str, Any]) -> list[str]:
    """Return only the command choices the current request can honor."""
    available = _available_decision_kinds(params)
    if available:
        choices: list[str] = []
        if "accept" in available:
            choices.append("once")
        if "acceptForSession" in available:
            choices.append("session")
        if {
            "acceptWithExecpolicyAmendment",
            "applyNetworkPolicyAmendment",
        } & available:
            choices.append("always")
        if {"decline", "cancel"} & available:
            choices.append("deny")
        return _normalize_ui_choices(choices)

    choices = ["once", "session"]
    if (
        params.get("proposedExecpolicyAmendment")
        or params.get("proposedNetworkPolicyAmendments")
    ):
        choices.append("always")
    choices.append("deny")
    return choices


def _persistent_command_decision(params: dict[str, Any]) -> Any:
    """Build the exact persistent decision proposed by Codex, if any."""
    available = params.get("availableDecisions")
    if isinstance(available, list):
        for decision in available:
            if not isinstance(decision, dict):
                continue
            if "acceptWithExecpolicyAmendment" in decision:
                return copy.deepcopy(decision)
            if "applyNetworkPolicyAmendment" in decision:
                return copy.deepcopy(decision)

    execpolicy = params.get("proposedExecpolicyAmendment")
    if isinstance(execpolicy, list) and execpolicy:
        return {
            "acceptWithExecpolicyAmendment": {
                "execpolicy_amendment": copy.deepcopy(execpolicy),
            }
        }
    network = params.get("proposedNetworkPolicyAmendments")
    if isinstance(network, list) and network and isinstance(network[0], dict):
        return {
            "applyNetworkPolicyAmendment": {
                "network_policy_amendment": copy.deepcopy(network[0]),
            }
        }
    return "acceptForSession"


def command_response(params: dict[str, Any], choice: str) -> dict[str, Any]:
    if choice == "once":
        decision: Any = "accept"
    elif choice == "session":
        decision = "acceptForSession"
    elif choice == "always":
        decision = _persistent_command_decision(params)
    else:
        decision = "decline"
    return {"decision": decision}


def file_change_response(choice: str) -> dict[str, Any]:
    if choice == "once":
        return {"decision": "accept"}
    if choice in {"session", "always"}:
        return {"decision": "acceptForSession"}
    return {"decision": "decline"}


def _command_prompt(params: dict[str, Any], cwd_fallback: str) -> tuple[str, str]:
    command = str(params.get("command") or "Codex command")
    cwd = str(params.get("cwd") or cwd_fallback or "<unknown>")
    reason = str(params.get("reason") or "").strip()
    description = f"Codex requests command execution in {cwd}"
    if reason:
        description += f" — {reason}"
    if params.get("proposedExecpolicyAmendment"):
        description += "。可将 Codex 提议的同类命令规则设为始终允许。"
    elif params.get("proposedNetworkPolicyAmendments"):
        description += "。可将 Codex 提议的网络规则设为始终允许。"
    return command, description


def _file_change_prompt(session: Any, params: dict[str, Any]) -> tuple[str, str]:
    reason = str(params.get("reason") or "").strip()
    grant_root = str(params.get("grantRoot") or "").strip()
    item_id = str(params.get("itemId") or "")
    lookup = getattr(session, "_lookup_pending_file_change", None)
    summary = lookup(item_id) if callable(lookup) else None
    parts = [part for part in (reason, summary) if part]
    if grant_root:
        parts.append(f"grants write to {grant_root}")
    description = "; ".join(parts) or "Codex requests to apply a patch"
    command = f"apply_patch: {summary or reason}" if (summary or reason) else "apply_patch"
    return command, description


def _ask_callback(
    callback: Any,
    command: str,
    description: str,
    choices: list[str],
) -> str:
    if callback is None:
        return "deny"
    return callback(
        command,
        description,
        allow_permanent="always" in choices,
        approval_choices=choices,
    )


def _permission_prompt(params: dict[str, Any]) -> tuple[str, str]:
    permissions = params.get("permissions")
    try:
        preview = json.dumps(
            permissions if isinstance(permissions, dict) else {},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):
        preview = "{}"
    if len(preview) > 3500:
        preview = preview[:3497] + "..."

    cwd = str(params.get("cwd") or "<unknown>")
    reason = str(params.get("reason") or "").strip()
    description = f"Codex requests additional permissions in {cwd}"
    if reason:
        description += f" — {reason}"
    description += "。“始终允许”仅作用于当前 Codex 会话。"
    return f"request_permissions {preview}", description


def _elicitation_meta(params: dict[str, Any]) -> dict[str, Any]:
    """Return the OpenAI elicitation metadata when it is object-shaped."""
    meta = params.get("_meta")
    return meta if isinstance(meta, dict) else {}


def _is_computer_use_elicitation(params: dict[str, Any]) -> bool:
    """Identify the narrow Computer Use authorization elicitation surface."""
    meta = _elicitation_meta(params)
    connector_id = str(meta.get("connector_id") or "").strip().lower()
    if connector_id == _COMPUTER_USE_CONNECTOR_ID:
        return True
    # Keep a compatibility fallback for hosts that omit extended metadata but
    # preserve the bundled connector's server identity.
    server_name = str(params.get("serverName") or "").strip().lower()
    return server_name in {_COMPUTER_USE_CONNECTOR_ID, "computer_use"}


def _computer_use_prompt(
    params: dict[str, Any],
) -> tuple[str, str, list[str]]:
    """Build a redaction-friendly Gateway prompt from a CUA elicitation."""
    meta = _elicitation_meta(params)
    tool_params = meta.get("tool_params")
    if not isinstance(tool_params, dict):
        tool_params = {}
    bundle_id = str(tool_params.get("app") or "").strip()

    display_name = ""
    displayed = meta.get("tool_params_display")
    if isinstance(displayed, list):
        for entry in displayed:
            if not isinstance(entry, dict) or entry.get("name") != "app":
                continue
            display_name = str(entry.get("value") or "").strip()
            if display_name:
                break

    app_label = display_name or bundle_id or "requested app"
    command = f"computer_use app={bundle_id or app_label}"
    message = str(params.get("message") or "").strip()
    description = message or f'Allow Computer Use to use "{app_label}"?'
    subtitle = str(meta.get("subtitle") or "").strip()
    if subtitle:
        description += f" — {subtitle}"
    risk = str(meta.get("riskLevel") or "").strip()
    if risk:
        description += f" (risk: {risk})"

    persist = meta.get("persist")
    if isinstance(persist, str):
        persist = [persist]
    choices = ["once"]
    if isinstance(persist, list) and "session" in persist:
        choices.append("session")
    if isinstance(persist, list) and "always" in persist:
        choices.append("always")
        description += "。“始终允许”会写入 Codex 的 Computer Use 应用策略。"
    choices.append("deny")
    return command, description, choices


def computer_use_elicitation_response(choice: str) -> dict[str, Any]:
    """Map a Hermes approval choice to the MCP elicitation response schema."""
    if choice not in {"once", "session", "always"}:
        return {"action": "decline", "content": None, "_meta": None}

    response: dict[str, Any] = {
        "action": "accept",
        "content": None,
        "_meta": None,
    }
    if choice in {"session", "always"}:
        response["_meta"] = {"persist": choice}
    return response


def wrap_server_request_handler(
    original: Callable,
    *,
    patch_command_file: bool = True,
    patch_permissions: bool = True,
    patch_computer_use: bool = True,
) -> Callable:
    """Render complete Codex approval choices and delegate unrelated requests."""

    @functools.wraps(original)
    def handle_server_request(self, req: dict[str, Any]) -> None:
        if not isinstance(req, dict):
            return original(self, req)
        method = req.get("method")
        params = req.get("params")
        if not isinstance(params, dict):
            params = {}

        if patch_command_file and method in {_COMMAND_METHOD, _FILE_METHOD}:
            client = getattr(self, "_client", None)
            if client is None:
                return None
            routing = getattr(self, "_routing", None)
            if method == _COMMAND_METHOD:
                if getattr(routing, "auto_approve_exec", False):
                    client.respond(req.get("id"), {"decision": "accept"})
                    return None
                command, description = _command_prompt(
                    params,
                    str(getattr(self, "_cwd", "") or "<unknown>"),
                )
                choices = command_approval_choices(params)
            else:
                if getattr(routing, "auto_approve_apply_patch", False):
                    client.respond(req.get("id"), {"decision": "accept"})
                    return None
                command, description = _file_change_prompt(self, params)
                choices = ["once", "session", "deny"]

            choice = "deny"
            try:
                choice = _ask_callback(
                    getattr(self, "_approval_callback", None),
                    command,
                    description,
                    choices,
                )
            except Exception:
                logger.exception("Codex command/file approval callback raised")
            response = (
                command_response(params, choice)
                if method == _COMMAND_METHOD
                else file_change_response(choice)
            )
            client.respond(req.get("id"), response)
            return None

        if (
            patch_computer_use
            and method == _ELICITATION_METHOD
            and _is_computer_use_elicitation(params)
        ):
            client = getattr(self, "_client", None)
            if client is None:
                return None
            command, description, choices = _computer_use_prompt(params)
            choice = "deny"
            try:
                choice = _ask_callback(
                    getattr(self, "_approval_callback", None),
                    command,
                    description,
                    choices,
                )
            except Exception:
                logger.exception("Codex Computer Use approval callback raised")
            client.respond(
                req.get("id"),
                computer_use_elicitation_response(choice),
            )
            return None

        if not (patch_permissions and method == _PERMISSION_METHOD):
            return original(self, req)
        client = getattr(self, "_client", None)
        if client is None:
            return None

        command, description = _permission_prompt(params)
        choice = "deny"
        try:
            choice = _ask_callback(
                getattr(self, "_approval_callback", None),
                command,
                description,
                ["once", "session", "deny"],
            )
        except Exception:
            logger.exception("Codex permission approval callback raised")
        client.respond(
            req.get("id"),
            permission_response(params.get("permissions"), choice),
        )
        return None

    setattr(handle_server_request, _REQUEST_MARKER, True)
    return handle_server_request


def _upstream_handles_permission_profiles(session_cls, handler: Callable) -> bool:
    """Behaviorally detect a current upstream granted-subset implementation."""

    responses: list[dict[str, Any]] = []

    class ProbeClient:
        def respond(self, _request_id, result):
            responses.append(result)

        def respond_error(self, *_args, **_kwargs):
            return None

    probe = object.__new__(session_cls)
    probe._client = ProbeClient()
    probe._approval_callback = lambda *_args, **_kwargs: "once"
    probe._routing = SimpleNamespace(
        auto_approve_exec=False,
        auto_approve_apply_patch=False,
    )
    probe._cwd = "/tmp"
    try:
        handler(
            probe,
            {
                "id": "permission-hotfix-probe",
                "method": _PERMISSION_METHOD,
                "params": {
                    "cwd": "/tmp",
                    "permissions": {"network": {"enabled": True}},
                },
            },
        )
    except Exception:
        return False
    return bool(
        responses
        and isinstance(responses[-1], dict)
        and isinstance(responses[-1].get("permissions"), dict)
    )


def _upstream_handles_computer_use_elicitation(
    session_cls,
    handler: Callable,
) -> bool:
    """Detect whether upstream already asks and accepts Computer Use access."""
    responses: list[dict[str, Any]] = []

    class ProbeClient:
        def respond(self, _request_id, result):
            responses.append(result)

        def respond_error(self, *_args, **_kwargs):
            return None

    probe = object.__new__(session_cls)
    probe._client = ProbeClient()
    probe._approval_callback = lambda *_args, **_kwargs: "once"
    probe._routing = SimpleNamespace(
        auto_approve_exec=False,
        auto_approve_apply_patch=False,
    )
    probe._cwd = "/tmp"
    try:
        handler(
            probe,
            {
                "id": "computer-use-hotfix-probe",
                "method": _ELICITATION_METHOD,
                "params": {
                    "serverName": "computer-use",
                    "threadId": "probe-thread",
                    "turnId": "probe-turn",
                    "mode": "openai/form",
                    "message": 'Allow Computer Use to use "Notes"?',
                    "requestedSchema": {},
                    "_meta": {
                        "connector_id": _COMPUTER_USE_CONNECTOR_ID,
                        "persist": ["session", "always"],
                        "tool_params": {"app": "com.apple.Notes"},
                    },
                },
            },
        )
    except Exception:
        return False
    return bool(
        responses
        and isinstance(responses[-1], dict)
        and responses[-1].get("action") == "accept"
    )


def patch_codex_gateway_approvals() -> str:
    """Install missing Gateway approval protocol patches once."""
    from agent.transports.codex_app_server_session import CodexAppServerSession

    statuses: list[str] = []
    original_init = CodexAppServerSession.__init__
    if getattr(original_init, _INIT_MARKER, False):
        statuses.append("gateway callback already patched")
    else:
        CodexAppServerSession.__init__ = wrap_session_init(original_init)
        statuses.append("gateway callback patched")

    handler = CodexAppServerSession._handle_server_request
    if getattr(handler, _REQUEST_MARKER, False):
        statuses.append("server requests already patched")
    else:
        patch_permissions = not _upstream_handles_permission_profiles(
            CodexAppServerSession,
            handler,
        )
        patch_computer_use = not _upstream_handles_computer_use_elicitation(
            CodexAppServerSession,
            handler,
        )
        CodexAppServerSession._handle_server_request = wrap_server_request_handler(
            handler,
            patch_command_file=True,
            patch_permissions=patch_permissions,
            patch_computer_use=patch_computer_use,
        )
        statuses.append("command/file approval choices patched")
        statuses.append(
            "permission requests patched"
            if patch_permissions
            else "upstream handles permission profiles; skipped"
        )
        statuses.append(
            "Computer Use elicitations patched"
            if patch_computer_use
            else "upstream handles Computer Use elicitations; skipped"
        )
    return ", ".join(statuses)
