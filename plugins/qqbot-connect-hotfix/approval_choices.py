"""Render every Codex approval scope that QQ can faithfully return.

Hermes 0.18.2's QQ keyboard understands only allow-once, allow-always, and
deny.  Its Codex adapter also sets ``allow_permanent=False`` for command and
file approvals, leaving QQ users with only two buttons even though app-server
supports ``acceptForSession``.  The Codex compatibility plugin records the
request's exact UI choices on the existing Gateway approval entry.  This patch
reads that short-lived entry, renders a scope-complete keyboard, and adds the
missing ``allow-session`` callback vocabulary.
"""

from __future__ import annotations

import functools
import logging
import re
from typing import Any, Callable

logger = logging.getLogger(__name__)

_SEND_MARKER = "_qq_codex_approval_choices_hotfix_wrapped"
_PARSER_MARKER = "_qq_codex_approval_parser_hotfix"
_UI_CHOICES_KEY = "codex_approval_choices"
_TOKEN_STORE_ATTR = "_qq_shared_approval_tokens"
_APPROVAL_DATA_RE = re.compile(
    r"^approve:(.+):(allow-once|allow-session|allow-always|deny)$"
)


def parse_approval_button_data(button_data: str):
    """Parse the extended callback vocabulary without exposing session data."""
    match = _APPROVAL_DATA_RE.match(button_data or "")
    if not match:
        return None
    return match.group(1), match.group(2)


setattr(parse_approval_button_data, _PARSER_MARKER, True)


def _real_session_key(adapter: Any, public_key: str) -> str:
    store = getattr(adapter, _TOKEN_STORE_ATTR, None)
    if isinstance(store, dict):
        record = store.get(public_key)
        if isinstance(record, dict):
            real_key = str(record.get("session_key") or "")
            if real_key:
                return real_key
    return public_key


def _pending_choices(adapter: Any, public_key: str) -> list[str]:
    """Read choices from the already-blocked Gateway request."""
    try:
        from tools import approval

        session_key = _real_session_key(adapter, public_key)
        with approval._lock:
            queue = approval._gateway_queues.get(session_key) or []
            entry = queue[0] if queue else None
            data = getattr(entry, "data", None)
            value = data.get(_UI_CHOICES_KEY) if isinstance(data, dict) else None
        if not isinstance(value, list):
            return []
        allowed = {"once", "session", "always", "deny"}
        requested = {str(item) for item in value if str(item) in allowed}
        return [
            choice
            for choice in ("once", "session", "always", "deny")
            if choice in requested
        ]
    except Exception:
        logger.exception("QQ approval choices patch could not inspect pending request")
        return []


def _approval_keyboard(session_key: str, choices: list[str]):
    from gateway.platforms.qqbot import keyboards

    specs = {
        "once": (
            "allow-once",
            "✅ 本次允许",
            "已允许本次",
            1,
        ),
        "session": (
            "allow-session",
            "🕒 会话允许",
            "已允许会话",
            1,
        ),
        "always": (
            "allow-always",
            "⭐ 始终允许同类",
            "已始终允许",
            1,
        ),
        "deny": (
            "deny",
            "❌ 拒绝",
            "已拒绝",
            0,
        ),
    }
    buttons = []
    for choice in choices:
        decision, label, visited, style = specs[choice]
        buttons.append(
            keyboards._make_callback_button(
                btn_id=f"approval-{choice}",
                label=label,
                visited_label=visited,
                data=f"{keyboards.APPROVAL_BUTTON_PREFIX}{session_key}:{decision}",
                style=style,
                group_id="approval",
            )
        )

    # Two buttons per row remains legible on mobile and stays below QQ's
    # existing keyboard dimensions.
    rows = [
        keyboards.KeyboardRow(buttons=buttons[index : index + 2])
        for index in range(0, len(buttons), 2)
    ]
    return keyboards.InlineKeyboard(
        content=keyboards.KeyboardContent(rows=rows)
    )


def wrap_send_approval_request(original: Callable) -> Callable:
    @functools.wraps(original)
    async def send_approval_request(self, chat_id, req, reply_to=None):
        choices = _pending_choices(self, str(getattr(req, "session_key", "") or ""))
        if not choices:
            return await original(self, chat_id, req, reply_to=reply_to)

        from gateway.platforms.qqbot.keyboards import build_approval_text

        return await self.send_with_keyboard(
            chat_id,
            build_approval_text(req),
            _approval_keyboard(req.session_key, choices),
            reply_to=reply_to,
        )

    setattr(send_approval_request, _SEND_MARKER, True)
    return send_approval_request


def patch_codex_approval_choices(adapter_cls) -> str:
    """Patch QQ parsing, choice mapping, and dynamic approval rendering."""
    from gateway.platforms.qqbot import adapter as adapter_module
    from gateway.platforms.qqbot import keyboards

    statuses: list[str] = []
    keyboards.parse_approval_button_data = parse_approval_button_data
    adapter_module.parse_approval_button_data = parse_approval_button_data
    statuses.append("approval session parser patched")

    mapping = dict(getattr(adapter_cls, "_APPROVAL_BUTTON_TO_CHOICE", {}) or {})
    mapping["allow-session"] = "session"
    adapter_cls._APPROVAL_BUTTON_TO_CHOICE = mapping
    statuses.append("approval session mapping patched")

    sender = adapter_cls.send_approval_request
    if getattr(sender, _SEND_MARKER, False):
        statuses.append("approval choices sender already patched")
    else:
        adapter_cls.send_approval_request = wrap_send_approval_request(sender)
        statuses.append("approval choices sender patched")
    return ", ".join(statuses)
