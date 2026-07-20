"""Regression checks for the persistent Codex app-server phase hotfix."""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path


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

    if old_home is None:
        os.environ.pop("HERMES_HOME", None)
    else:
        os.environ["HERMES_HOME"] = old_home

    print("commentary_forwarded=true")
    print("final_answer_suppressed=true")
    print("unknown_terminal_suppressed=true")
    print("image_generation_materialized=true")
    print("empty_image_final_recovered=true")
    print(f"install_status={status}")


if __name__ == "__main__":
    main()
