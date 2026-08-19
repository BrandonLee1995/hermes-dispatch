"""QQ outbound send compatibility patches."""

from __future__ import annotations

import functools
import logging
import uuid
from typing import Any, Awaitable, Callable, Dict

logger = logging.getLogger(__name__)

_EXPIRED_REPLY_WRAPPER = "_qqbot_expired_reply_fallback_wrapped"


def is_expired_reply_error(error: object) -> bool:
    """Return whether QQ rejected an expired reply message anchor.

    QQ currently reports Chinese errors such as ``msg_id已过期``.  Keep
    conservative English aliases as gateways and SDK layers sometimes render
    the same field as ``message_id`` or ``message id``.
    """

    text = str(error or "").lower()
    has_reply_id = any(
        marker in text for marker in ("msg_id", "message_id", "message id")
    )
    has_expiry = any(
        marker in text for marker in ("expired", "expire", "expiration", "过期")
    )
    return has_reply_id and has_expiry


async def _send_with_expired_reply_fallback(
    send: Callable[[object], Awaitable[Any]],
    *,
    reply_to: object,
    log_tag: str,
    send_kind: str,
):
    """Try the referenced send, then retry once without the expired anchor."""

    try:
        result = await send(reply_to)
    except Exception as exc:
        if not reply_to or not is_expired_reply_error(exc):
            raise
        logger.warning(
            "qqbot-connect-hotfix: %s reply anchor expired for %s; "
            "retrying once as a standalone message: %s",
            send_kind,
            log_tag,
            exc,
        )
        try:
            return await send(None)
        except Exception as fallback_exc:
            raise RuntimeError(
                "QQ standalone fallback failed after expired reply anchor; "
                f"fallback={fallback_exc}; original={exc}"
            ) from fallback_exc

    if (
        reply_to
        and not getattr(result, "success", True)
        and is_expired_reply_error(getattr(result, "error", None))
    ):
        logger.warning(
            "qqbot-connect-hotfix: %s reply anchor expired for %s; "
            "retrying once as a standalone message: %s",
            send_kind,
            log_tag,
            getattr(result, "error", ""),
        )
        return await send(None)
    return result


def patch_expired_reply_fallback(QQAdapter):
    """Retry QQ text/keyboard sends once without an expired ``reply_to``.

    Wrapping the three low-level text senders covers normal chunk delivery and
    ``send_with_keyboard`` without duplicating either caller.  The keyboard
    object is passed through unchanged on the standalone retry.
    """

    patched = []

    for method_name, send_kind in (
        ("_send_c2c_text", "C2C text/keyboard"),
        ("_send_group_text", "group text/keyboard"),
    ):
        original = getattr(QQAdapter, method_name, None)
        if original is None or getattr(original, _EXPIRED_REPLY_WRAPPER, False):
            continue

        def make_text_wrapper(original_method, kind):
            @functools.wraps(original_method)
            async def wrapped(
                self,
                target_id: str,
                content: str,
                reply_to=None,
                keyboard=None,
            ):
                async def send(anchor):
                    return await original_method(
                        self,
                        target_id,
                        content,
                        anchor,
                        keyboard,
                    )

                return await _send_with_expired_reply_fallback(
                    send,
                    reply_to=reply_to,
                    log_tag=str(target_id),
                    send_kind=kind,
                )

            setattr(wrapped, _EXPIRED_REPLY_WRAPPER, True)
            return wrapped

        setattr(QQAdapter, method_name, make_text_wrapper(original, send_kind))
        patched.append(method_name)

    original_guild = getattr(QQAdapter, "_send_guild_text", None)
    if original_guild is not None and not getattr(
        original_guild, _EXPIRED_REPLY_WRAPPER, False
    ):
        @functools.wraps(original_guild)
        async def _send_guild_text(
            self,
            channel_id: str,
            content: str,
            reply_to=None,
        ):
            async def send(anchor):
                return await original_guild(self, channel_id, content, anchor)

            return await _send_with_expired_reply_fallback(
                send,
                reply_to=reply_to,
                log_tag=str(channel_id),
                send_kind="guild text",
            )

        setattr(_send_guild_text, _EXPIRED_REPLY_WRAPPER, True)
        QQAdapter._send_guild_text = _send_guild_text
        patched.append("_send_guild_text")

    if patched:
        logger.info(
            "qqbot-connect-hotfix: patched expired reply fallback for %s",
            ", ".join(patched),
        )


def patch_plain_text_retry(QQAdapter):
    original_c2c = QQAdapter._send_c2c_text
    original_group = QQAdapter._send_group_text
    if getattr(original_c2c, "_qqbot_plain_text_retry_wrapped", False):
        return

    async def _send_c2c_text(self, openid: str, content: str, reply_to=None, keyboard=None):
        try:
            return await original_c2c(self, openid, content, reply_to, keyboard)
        except RuntimeError as exc:
            if should_retry_plain_text(self, exc):
                logger.warning(
                    "qqbot-connect-hotfix: markdown C2C send failed for %s; retrying plain text: %s",
                    openid,
                    exc,
                )
                return await send_plain_text(self, "c2c", openid, content, reply_to, keyboard)
            raise

    async def _send_group_text(self, group_openid: str, content: str, reply_to=None, keyboard=None):
        try:
            return await original_group(self, group_openid, content, reply_to, keyboard)
        except RuntimeError as exc:
            if should_retry_plain_text(self, exc):
                logger.warning(
                    "qqbot-connect-hotfix: markdown group send failed for %s; retrying plain text: %s",
                    group_openid,
                    exc,
                )
                return await send_plain_text(self, "group", group_openid, content, reply_to, keyboard)
            raise

    _send_c2c_text.__name__ = getattr(original_c2c, "__name__", "_send_c2c_text")
    _send_c2c_text.__qualname__ = getattr(original_c2c, "__qualname__", "QQAdapter._send_c2c_text")
    _send_c2c_text._qqbot_plain_text_retry_wrapped = True
    _send_group_text.__name__ = getattr(original_group, "__name__", "_send_group_text")
    _send_group_text.__qualname__ = getattr(original_group, "__qualname__", "QQAdapter._send_group_text")
    _send_group_text._qqbot_plain_text_retry_wrapped = True
    QQAdapter._send_c2c_text = _send_c2c_text
    QQAdapter._send_group_text = _send_group_text
    logger.info("qqbot-connect-hotfix: patched QQAdapter text send with plain-text retry")


def should_retry_plain_text(self, exc: Exception) -> bool:
    if not getattr(self, "_markdown_support", False):
        return False
    text = str(exc).lower()
    return "invalid request" in text or "markdown" in text


async def send_plain_text(self, target_type: str, target_id: str, content: str, reply_to=None, keyboard=None):
    from gateway.platforms.base import SendResult
    from gateway.platforms.qqbot.constants import MSG_TYPE_TEXT

    msg_seq = self._next_msg_seq(reply_to or target_id)
    body: Dict[str, Any] = {
        "content": content[: self.MAX_MESSAGE_LENGTH],
        "msg_type": MSG_TYPE_TEXT,
        "msg_seq": msg_seq,
    }
    if reply_to:
        body["msg_id"] = reply_to
        body["message_reference"] = {"message_id": reply_to}
    if keyboard is not None:
        body["keyboard"] = keyboard.to_dict()

    path = f"/v2/users/{target_id}/messages" if target_type == "c2c" else f"/v2/groups/{target_id}/messages"
    data = await self._api_request("POST", path, body)
    return SendResult(
        success=True,
        message_id=str(data.get("id", uuid.uuid4().hex[:12])),
        raw_response=data,
    )


def patch_media_caption_retry(QQAdapter):
    original = QQAdapter._send_media
    if getattr(original, "_qqbot_media_caption_retry_wrapped", False):
        return

    async def _send_media(self, chat_id: str, media_source: str, file_type: int, kind: str, caption=None, reply_to=None, file_name=None):
        result = await original(self, chat_id, media_source, file_type, kind, caption, reply_to, file_name=file_name)
        if result.success or not caption:
            return result
        if "invalid request" not in str(result.error or "").lower():
            return result
        logger.warning(
            "qqbot-connect-hotfix: media send with caption failed for %s; retrying without caption: %s",
            chat_id,
            result.error,
        )
        return await original(self, chat_id, media_source, file_type, kind, None, reply_to, file_name=file_name)

    _send_media.__name__ = getattr(original, "__name__", "_send_media")
    _send_media.__qualname__ = getattr(original, "__qualname__", "QQAdapter._send_media")
    _send_media.__doc__ = getattr(original, "__doc__", None)
    _send_media._qqbot_media_caption_retry_wrapped = True
    QQAdapter._send_media = _send_media
    logger.info("qqbot-connect-hotfix: patched QQAdapter media send with caption retry")
