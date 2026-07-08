# /// script
# requires-python = ">=3.13"
# ///
"""Patch Hermes MCP QQBot target parsing for explicit group OpenIDs.

How to run:
    python /opt/data/scripts/hermes_mcp_qqbot_target_patch.py

This removable compatibility shim compensates for upstream Hermes
``tools.send_message_tool._parse_target_ref`` not treating QQBot 32-character
OpenIDs from ``channel_directory.json`` as explicit send targets. Without this,
``messages_send(target="qqbot:<group_openid>")`` can fall through to home-channel
delivery instead of posting to ``/v2/groups/{group_openid}/messages``.
"""

from __future__ import annotations

import logging
from pathlib import Path
import re
from collections.abc import Callable
from types import ModuleType
from typing import TypedDict

logger = logging.getLogger(__name__)

ParseResult = tuple[str | None, str | None, bool]
ParseTargetRef = Callable[[str, str], ParseResult]


class QqbotMediaSendResult(TypedDict, total=False):
    success: bool
    platform: str
    chat_id: str
    message_id: str
    error: str

_QQBOT_OPENID_RE = re.compile(r"^[A-Fa-f0-9]{32}$")
_QQBOT_TYPED_OPENID_RE = re.compile(r"^(?:group|dm|c2c|user):([A-Fa-f0-9]{32})$")
_IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif"})


def _parse_qqbot_target_ref(target_ref: str) -> ParseResult | None:
    stripped = target_ref.strip()
    typed_match = _QQBOT_TYPED_OPENID_RE.fullmatch(stripped)
    if typed_match:
        return typed_match.group(1), None, True
    if _QQBOT_OPENID_RE.fullmatch(stripped):
        return stripped, None, True
    return None


def install_qqbot_target_patch(send_message_tool_module: ModuleType) -> None:
    original = getattr(send_message_tool_module, "_parse_target_ref")
    if getattr(original, "_qqbot_openid_wrapped", False):
        _install_qqbot_media_patch(send_message_tool_module)
        return

    def _parse_target_ref(platform_name: str, target_ref: str) -> ParseResult:
        if platform_name == "qqbot":
            parsed = _parse_qqbot_target_ref(target_ref)
            if parsed is not None:
                return parsed
        return original(platform_name, target_ref)

    _parse_target_ref.__name__ = getattr(original, "__name__", "_parse_target_ref")
    _parse_target_ref.__qualname__ = getattr(original, "__qualname__", "_parse_target_ref")
    _parse_target_ref.__doc__ = getattr(original, "__doc__", None)
    _parse_target_ref._qqbot_openid_wrapped = True
    setattr(send_message_tool_module, "_parse_target_ref", _parse_target_ref)
    logger.info("hermes-mcp-http: patched QQBot explicit OpenID target parsing")
    _install_qqbot_media_patch(send_message_tool_module)


def _qqbot_media_file_type(media_path: str) -> int:
    from gateway.platforms.qqbot.constants import MEDIA_TYPE_FILE, MEDIA_TYPE_IMAGE

    suffix = Path(media_path).suffix.lower()
    if suffix in _IMAGE_EXTS:
        return MEDIA_TYPE_IMAGE
    return MEDIA_TYPE_FILE


def _install_qqbot_media_patch(send_message_tool_module: ModuleType) -> None:
    original = getattr(send_message_tool_module, "_send_to_platform")
    if getattr(original, "_qqbot_media_wrapped", False):
        return

    async def _send_to_platform(
        platform,
        pconfig,
        chat_id,
        message,
        thread_id=None,
        media_files=None,
        force_document=False,
    ):
        media_files = media_files or []
        platform_name = platform.value if hasattr(platform, "value") else str(platform)
        if platform_name == "qqbot" and media_files:
            return await _send_qqbot_media(pconfig, chat_id, message, media_files)
        return await original(
            platform,
            pconfig,
            chat_id,
            message,
            thread_id=thread_id,
            media_files=media_files,
            force_document=force_document,
        )

    _send_to_platform.__name__ = getattr(original, "__name__", "_send_to_platform")
    _send_to_platform.__qualname__ = getattr(original, "__qualname__", "_send_to_platform")
    _send_to_platform.__doc__ = getattr(original, "__doc__", None)
    _send_to_platform._qqbot_media_wrapped = True
    setattr(send_message_tool_module, "_send_to_platform", _send_to_platform)
    logger.info("hermes-mcp-http: patched QQBot MEDIA attachment delivery")


async def _send_qqbot_media(
    pconfig,
    chat_id: str,
    message: str,
    media_files,
) -> QqbotMediaSendResult:
    import httpx
    from gateway.platforms.qqbot.adapter import QQAdapter
    from gateway.platforms.qqbot.constants import MEDIA_TYPE_FILE, MSG_TYPE_MEDIA

    adapter = QQAdapter(pconfig)
    adapter._chat_type_map[str(chat_id)] = "group"
    adapter._http_client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
    try:
        last_result = None
        for index, item in enumerate(media_files):
            media_path = item[0]
            file_type = _qqbot_media_file_type(media_path)
            file_name = Path(media_path).name if file_type == MEDIA_TYPE_FILE else None
            _resolved_name, upload = await adapter._upload_local_file(
                "group",
                chat_id,
                media_path,
                file_type,
                file_name,
            )
            file_info = upload.get("file_info") or (upload.get("data", {}) or {}).get("file_info")
            if not file_info:
                return {"error": f"QQBot upload returned no file_info for {media_path}"}

            body = {
                "msg_type": MSG_TYPE_MEDIA,
                "media": {"file_info": file_info},
                "msg_seq": adapter._next_msg_seq(chat_id),
            }
            if index == 0 and message.strip():
                body["content"] = message[: adapter.MAX_MESSAGE_LENGTH]
            data = await adapter._api_request("POST", f"/v2/groups/{chat_id}/messages", body)
            last_result = {
                "success": True,
                "platform": "qqbot",
                "chat_id": chat_id,
                "message_id": str(data.get("id", "")),
            }
        return last_result or {"error": "QQBot media send had no files"}
    finally:
        await adapter._cleanup()


def install() -> None:
    from tools import send_message_tool

    install_qqbot_target_patch(send_message_tool)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    install()
    print("qqbot_target_patch=installed")
