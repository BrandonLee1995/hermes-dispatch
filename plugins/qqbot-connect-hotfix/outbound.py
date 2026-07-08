"""QQ outbound send compatibility patches."""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict

logger = logging.getLogger(__name__)


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
