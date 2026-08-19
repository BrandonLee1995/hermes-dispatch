"""Make Codex app-server turn deadlines configurable and session-local.

Hermes 0.20.0 hard-codes a 600 second wall-clock deadline in
``CodexAppServerSession.run_turn``.  Long-running foreground tools can still be
healthy at that point, but Hermes accepts the latest assistant commentary as a
terminal answer and leaves the Codex tool running behind it.

This compatibility layer changes only the default used when Hermes does not
pass an explicit timeout.  A value of ``0`` disables the wall-clock deadline;
interrupts, subprocess death detection, and the post-tool quiet watchdog stay
active in the upstream session implementation.
"""

from __future__ import annotations

import functools
import math
import os
import time
from typing import Any, Callable


TIMEOUT_ENV = "HERMES_CODEX_APP_SERVER_TURN_TIMEOUT_SECONDS"
_PATCH_MARKER = "_codex_app_server_long_turn_hotfix_wrapped"


def configured_turn_timeout(raw: str | None = None) -> float:
    """Return the configured timeout; infinity represents no wall deadline."""
    value = os.environ.get(TIMEOUT_ENV, "0") if raw is None else raw
    try:
        seconds = float(str(value).strip())
    except (TypeError, ValueError):
        seconds = 0.0
    if not math.isfinite(seconds) or seconds <= 0:
        return math.inf
    return seconds


def _is_turn_completed(note: Any) -> bool:
    return isinstance(note, dict) and note.get("method") == "turn/completed"


def wrap_session_run_turn(original: Callable) -> Callable:
    """Apply a configurable default without sharing state between sessions.

    The terminal-event flag and callback wrapper are created inside each call.
    Consequently, two Hermes sessions running concurrently cannot mutate each
    other's deadline or completion state.
    """

    @functools.wraps(original)
    def run_turn(
        self,
        user_input: Any,
        *,
        turn_timeout: float | None = None,
        notification_poll_timeout: float = 0.25,
        post_tool_quiet_timeout: float = 90.0,
    ):
        effective_timeout = (
            configured_turn_timeout()
            if turn_timeout is None
            else configured_turn_timeout(str(turn_timeout))
        )
        terminal_seen = False
        delegate = getattr(self, "_on_event", None)

        def observe(note: Any) -> None:
            nonlocal terminal_seen
            if _is_turn_completed(note):
                terminal_seen = True
            if delegate is not None:
                delegate(note)

        # CodexAppServerSession is deliberately single-caller, while every
        # Hermes conversation owns a distinct session object.  Replacing this
        # instance callback for the duration of one call therefore remains
        # isolated from all other conversations.
        setattr(self, "_on_event", observe)
        started = time.monotonic()
        try:
            result = original(
                self,
                user_input,
                turn_timeout=effective_timeout,
                notification_poll_timeout=notification_poll_timeout,
                post_tool_quiet_timeout=post_tool_quiet_timeout,
            )
        finally:
            if getattr(self, "_on_event", None) is observe:
                setattr(self, "_on_event", delegate)

        # Hermes 0.20.0 treats the most recent assistant message as final when
        # its finite deadline expires without turn/completed.  Convert that
        # ambiguous return into a real timeout, interrupt it, and retire the
        # subprocess so commentary is never persisted or delivered as success.
        elapsed = time.monotonic() - started
        deadline_slack = min(
            max(0.01, notification_poll_timeout * 2),
            effective_timeout * 0.01,
        )
        reached_deadline = (
            math.isfinite(effective_timeout)
            and elapsed >= max(0.0, effective_timeout - deadline_slack)
        )
        if (
            reached_deadline
            and not terminal_seen
            and not getattr(result, "interrupted", False)
            and getattr(result, "error", None) is None
        ):
            issue_interrupt = getattr(self, "_issue_interrupt", None)
            if callable(issue_interrupt):
                issue_interrupt(getattr(result, "turn_id", None))
            result.interrupted = True
            result.error = (
                f"codex app-server turn timed out after {effective_timeout:g}s "
                "without turn/completed"
            )
            result.should_retire = True
        return result

    setattr(run_turn, _PATCH_MARKER, True)
    run_turn._codex_app_server_long_turn_original = original
    return run_turn


def patch_codex_app_server_turn_timeout() -> str:
    """Patch the installed Hermes session class once."""
    from agent.transports.codex_app_server_session import CodexAppServerSession

    original = CodexAppServerSession.run_turn
    if getattr(original, _PATCH_MARKER, False):
        return "already patched"
    CodexAppServerSession.run_turn = wrap_session_run_turn(original)
    configured = configured_turn_timeout()
    label = "unlimited" if math.isinf(configured) else f"{configured:g}s"
    return f"patched Codex turn wall deadline (default={label})"
