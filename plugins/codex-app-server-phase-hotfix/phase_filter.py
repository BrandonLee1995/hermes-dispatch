"""Filter Codex final answers out of Hermes' interim-message bridge.

The Codex session projector consumes the same notifications independently, so
filtering here changes display delivery only. Final text still becomes the
turn's canonical response and follows Hermes' normal final-send path.
"""

from __future__ import annotations

import functools
from types import SimpleNamespace
from typing import Any, Callable


_PATCH_MARKER = "_codex_app_server_phase_hotfix_wrapped"


def _event_item(note: Any) -> tuple[str, dict[str, Any] | None]:
    if not isinstance(note, dict):
        return "", None
    method = str(note.get("method") or "")
    params = note.get("params")
    if not isinstance(params, dict):
        return method, None
    item = params.get("item")
    return method, item if isinstance(item, dict) else None


def _message_phase(item: dict[str, Any]) -> str:
    phase = item.get("phase")
    return phase.strip().lower() if isinstance(phase, str) else ""


def _is_completed_agent_message(method: str, item: dict[str, Any] | None) -> bool:
    return bool(
        method == "item/completed"
        and item is not None
        and item.get("type") == "agentMessage"
    )


def _proves_turn_continues(method: str, item: dict[str, Any] | None) -> bool:
    """Return true when an unknown-phase message cannot have been final.

    Older providers may omit ``phase``. Hold such a message briefly: a later
    item proves it was commentary, while ``turn/completed`` proves it was the
    terminal answer and it must not use the interim channel.
    """
    if method == "item/started":
        return True
    return bool(
        method == "item/completed"
        and item is not None
        and item.get("type") != "agentMessage"
    )


def wrap_event_bridge_factory(original: Callable) -> Callable:
    """Wrap a Hermes Codex display-bridge factory with phase-aware delivery."""

    @functools.wraps(original)
    def factory(agent):
        delegate = original(agent)
        pending_unknown: dict[str, Any] | None = None

        def flush_pending_unknown() -> None:
            nonlocal pending_unknown
            if pending_unknown is not None:
                note = pending_unknown
                pending_unknown = None
                delegate(note)

        def on_event(note: dict[str, Any]) -> None:
            nonlocal pending_unknown
            method, item = _event_item(note)

            if _is_completed_agent_message(method, item):
                assert item is not None
                phase = _message_phase(item)
                if phase == "final_answer":
                    # Any older unknown-phase message before an explicit final
                    # answer was commentary and can now be released.
                    flush_pending_unknown()
                    return
                if phase == "commentary":
                    flush_pending_unknown()
                    delegate(note)
                    return

                # Phase unknown: keep only the newest candidate. Seeing another
                # assistant message proves the previous candidate was interim.
                flush_pending_unknown()
                pending_unknown = note
                return

            if method == "turn/completed":
                # A still-pending unknown message is the final answer. The
                # normal final-send path will deliver it exactly once.
                pending_unknown = None
                delegate(note)
                return

            if _proves_turn_continues(method, item):
                flush_pending_unknown()
            delegate(note)

        return on_event

    setattr(factory, _PATCH_MARKER, True)
    factory._codex_app_server_phase_hotfix_original = original
    return factory


def _factory_already_filters_final_answers(factory: Callable) -> bool:
    """Behaviorally detect an upstream phase-aware implementation."""
    emitted: list[dict[str, Any]] = []
    probe = SimpleNamespace(
        show_commentary=True,
        _emit_interim_assistant_message=emitted.append,
        _fire_stream_delta=None,
        _fire_reasoning_delta=None,
        tool_progress_callback=None,
        tool_start_callback=None,
        tool_complete_callback=None,
    )
    try:
        bridge = factory(probe)
        bridge(
            {
                "method": "item/completed",
                "params": {
                    "item": {
                        "type": "agentMessage",
                        "id": "phase-hotfix-probe",
                        "phase": "final_answer",
                        "text": "probe",
                    }
                },
            }
        )
    except Exception:
        return False
    return not emitted


def patch_codex_app_server_event_bridge() -> str:
    """Patch Hermes once, and no-op after Hermes gains an equivalent fix."""
    from agent import codex_runtime

    original = codex_runtime.make_codex_app_server_event_bridge
    if getattr(original, _PATCH_MARKER, False):
        return "already patched"
    if _factory_already_filters_final_answers(original):
        return "upstream already filters final_answer; skipped"
    codex_runtime.make_codex_app_server_event_bridge = wrap_event_bridge_factory(original)
    return "patched Codex agentMessage phase routing"
