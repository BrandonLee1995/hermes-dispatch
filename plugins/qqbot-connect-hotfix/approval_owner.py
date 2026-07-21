"""Bind shared-group approval buttons to the user who triggered the turn.

Hermes 0.18.2 authorizes QQ group approval clicks by extracting a user id from
the Gateway session key.  Shared group sessions intentionally omit that id
(``group_sessions_per_user: false``), so every click fails closed.  This patch
replaces the public button payload with a short-lived opaque token and keeps
the real session key plus requester id only in process memory.
"""

from __future__ import annotations

import functools
import logging
import secrets
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

_SEND_MARKER = "_qq_shared_approval_owner_send_hotfix_wrapped"
_DISPATCH_MARKER = "_qq_shared_approval_owner_dispatch_hotfix_wrapped"
_TOKEN_PREFIX = "qq-approval-v1."
_TOKEN_TTL_SECONDS = 300.0
_TOKEN_STORE_ATTR = "_qq_shared_approval_tokens"
_SLASH_MARKER = "_qq_shared_approval_owner_slash_hotfix_wrapped"
_PENDING_OWNER_BY_SESSION: dict[str, dict[str, Any]] = {}


def _token_store(adapter: Any) -> dict[str, dict[str, Any]]:
    store = getattr(adapter, _TOKEN_STORE_ATTR, None)
    if not isinstance(store, dict):
        store = {}
        setattr(adapter, _TOKEN_STORE_ATTR, store)
    return store


def _prune_tokens(store: dict[str, dict[str, Any]], now: float) -> None:
    for token, record in list(store.items()):
        if float(record.get("expires_at") or 0) <= now:
            store.pop(token, None)


def _prune_pending_owners(now: float) -> None:
    for session_key, record in list(_PENDING_OWNER_BY_SESSION.items()):
        if float(record.get("expires_at") or 0) <= now:
            _PENDING_OWNER_BY_SESSION.pop(session_key, None)


def _shared_group_session(adapter: Any, session_key: str) -> dict[str, str] | None:
    parser = getattr(adapter, "_parse_gateway_session_key", None)
    if not callable(parser):
        return None
    parsed = parser(session_key)
    if not isinstance(parsed, dict):
        return None
    if parsed.get("chat_type") not in {"group", "guild"}:
        return None
    if str(parsed.get("user_id") or "").strip():
        return None
    return parsed


def _current_requester_user_id() -> str:
    try:
        from gateway.session_context import get_session_env

        return str(get_session_env("HERMES_SESSION_USER_ID", "") or "").strip()
    except Exception:
        logger.exception("QQ approval owner patch could not read session user id")
        return ""


def wrap_send_exec_approval(original: Callable) -> Callable:
    """Issue an opaque owner-bound token for a shared group approval."""

    @functools.wraps(original)
    async def send_exec_approval(
        self,
        chat_id: str,
        command: str,
        session_key: str,
        description: str = "dangerous command",
        metadata=None,
        allow_permanent: bool = True,
        smart_denied: bool = False,
    ):
        parsed = _shared_group_session(self, session_key)
        if parsed is None:
            return await original(
                self,
                chat_id,
                command,
                session_key,
                description,
                metadata,
                allow_permanent,
                smart_denied,
            )

        requester = _current_requester_user_id()
        if not requester:
            logger.warning(
                "QQ shared-group approval has no requester identity; leaving "
                "the upstream fail-closed behavior in place (session=%s)",
                session_key,
            )
            return await original(
                self,
                chat_id,
                command,
                session_key,
                description,
                metadata,
                allow_permanent,
                smart_denied,
            )

        now = time.monotonic()
        store = _token_store(self)
        _prune_tokens(store, now)
        token = _TOKEN_PREFIX + secrets.token_urlsafe(12)
        store[token] = {
            "session_key": session_key,
            "requester_user_id": requester,
            "chat_id": str(parsed.get("chat_id") or chat_id),
            "expires_at": now + _TOKEN_TTL_SECONDS,
        }
        try:
            result = await original(
                self,
                chat_id,
                command,
                token,
                description,
                metadata,
                allow_permanent,
                smart_denied,
            )
        except Exception:
            store.pop(token, None)
            raise
        if not getattr(result, "success", False):
            store.pop(token, None)
        else:
            _prune_pending_owners(now)
            _PENDING_OWNER_BY_SESSION[session_key] = {
                "requester_user_id": requester,
                "chat_id": str(parsed.get("chat_id") or chat_id),
                "expires_at": now + _TOKEN_TTL_SECONDS,
                "token": token,
            }
        return result

    setattr(send_exec_approval, _SEND_MARKER, True)
    return send_exec_approval


def wrap_interaction_dispatch(original: Callable) -> Callable:
    """Resolve owner-bound approval tokens before Hermes parses session keys."""

    @functools.wraps(original)
    async def interaction_dispatch(self, event) -> None:
        from gateway.platforms.qqbot.keyboards import parse_approval_button_data

        approval = parse_approval_button_data(
            getattr(event, "button_data", None) or ""
        )
        if approval is None:
            return await original(self, event)
        token, decision = approval
        if not token.startswith(_TOKEN_PREFIX):
            return await original(self, event)

        store = _token_store(self)
        now = time.monotonic()
        record = store.get(token)
        if not isinstance(record, dict):
            _prune_tokens(store, now)
            logger.warning("Rejected unknown or expired QQ approval token")
            return None
        if float(record.get("expires_at") or 0) <= now:
            store.pop(token, None)
            logger.warning("Rejected expired QQ approval token")
            return None

        operator = str(getattr(event, "operator_openid", "") or "").strip()
        event_chat = str(
            getattr(event, "group_openid", "")
            or getattr(event, "guild_id", "")
            or ""
        ).strip()
        expected_user = str(record.get("requester_user_id") or "").strip()
        expected_chat = str(record.get("chat_id") or "").strip()
        pending_owner = _PENDING_OWNER_BY_SESSION.get(
            str(record.get("session_key") or "")
        )
        if not isinstance(pending_owner, dict) or pending_owner.get("token") != token:
            store.pop(token, None)
            logger.warning("Rejected stale QQ approval token")
            return None
        if not operator or operator != expected_user or event_chat != expected_chat:
            logger.warning(
                "Rejected QQ shared-group approval click: requester/chat mismatch"
            )
            return None

        choice_map = getattr(self, "_APPROVAL_BUTTON_TO_CHOICE", {})
        choice = choice_map.get(decision) if isinstance(choice_map, dict) else None
        if choice is None:
            logger.warning(
                "Rejected QQ shared-group approval with unknown decision %r",
                decision,
            )
            return None

        # Consume the nonce before resolution so double clicks and old buttons
        # can never authorize a later pending request in the same shared chat.
        store.pop(token, None)
        _PENDING_OWNER_BY_SESSION.pop(str(record.get("session_key") or ""), None)
        try:
            from tools.approval import resolve_gateway_approval

            count = resolve_gateway_approval(str(record["session_key"]), choice)
            logger.info(
                "QQ owner-bound button resolved %d approval(s) (choice=%s)",
                count,
                choice,
            )
        except Exception:
            logger.exception("QQ owner-bound approval resolution failed")
        return None

    setattr(interaction_dispatch, _DISPATCH_MARKER, True)
    return interaction_dispatch


def _source_platform_name(source: Any) -> str:
    platform = getattr(source, "platform", "")
    value = getattr(platform, "value", platform)
    return str(value or "").strip().lower()


def _shared_qq_group_key(session_key: str) -> bool:
    parts = str(session_key).split(":")
    return (
        len(parts) == 5
        and parts[:3] == ["agent", "main", "qqbot"]
        and parts[3] in {"group", "guild"}
        and bool(parts[4])
    )


def wrap_typed_approval_handler(original: Callable) -> Callable:
    """Restrict /approve and /deny in a shared QQ group to the requester."""

    @functools.wraps(original)
    async def typed_approval(self, event):
        source = getattr(event, "source", None)
        if source is None or _source_platform_name(source) != "qqbot":
            return await original(self, event)
        session_key = str(self._session_key_for_source(source) or "")
        if not _shared_qq_group_key(session_key):
            return await original(self, event)

        now = time.monotonic()
        _prune_pending_owners(now)
        record = _PENDING_OWNER_BY_SESSION.get(session_key)
        operator = str(getattr(source, "user_id", "") or "").strip()
        if (
            not isinstance(record, dict)
            or not operator
            or operator != str(record.get("requester_user_id") or "").strip()
        ):
            logger.warning(
                "Rejected typed QQ shared-group approval: requester mismatch"
            )
            return "Only the member who initiated this approval can resolve it."

        try:
            return await original(self, event)
        finally:
            _PENDING_OWNER_BY_SESSION.pop(session_key, None)

    setattr(typed_approval, _SLASH_MARKER, True)
    return typed_approval


def patch_shared_group_typed_approvals(slash_cls) -> str:
    """Install owner checks on QQ /approve and /deny handlers."""
    statuses: list[str] = []
    for name in ("_handle_approve_command", "_handle_deny_command"):
        handler = getattr(slash_cls, name)
        if getattr(handler, _SLASH_MARKER, False):
            statuses.append(f"{name} already patched")
            continue
        setattr(slash_cls, name, wrap_typed_approval_handler(handler))
        statuses.append(f"{name} patched")
    return ", ".join(statuses)


def patch_shared_group_approval_owners(adapter_cls) -> str:
    """Install the shared-group approval owner patch idempotently."""
    statuses: list[str] = []

    send = adapter_cls.send_exec_approval
    if getattr(send, _SEND_MARKER, False):
        statuses.append("approval sender already patched")
    else:
        adapter_cls.send_exec_approval = wrap_send_exec_approval(send)
        statuses.append("approval sender patched")

    dispatch = adapter_cls._default_interaction_dispatch
    if getattr(dispatch, _DISPATCH_MARKER, False):
        statuses.append("approval dispatcher already patched")
    else:
        adapter_cls._default_interaction_dispatch = wrap_interaction_dispatch(dispatch)
        statuses.append("approval dispatcher patched")

    return ", ".join(statuses)
