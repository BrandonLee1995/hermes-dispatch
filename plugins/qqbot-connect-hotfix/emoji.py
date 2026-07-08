"""QQ group mention text normalization helpers."""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


def patch_emoji_only_group_mentions(QQAdapter):
    original = QQAdapter._strip_at_mention
    if getattr(original, "_qqbot_emoji_only_wrapped", False):
        return

    def _strip_at_mention(content: str) -> str:
        stripped = original(content)
        normalized = describe_qq_face_only_message(stripped)
        if normalized != stripped:
            logger.info("qqbot-connect-hotfix: normalized emoji-only group mention")
        return normalized

    _strip_at_mention.__name__ = getattr(original, "__name__", "_strip_at_mention")
    _strip_at_mention.__qualname__ = getattr(
        original,
        "__qualname__",
        "QQAdapter._strip_at_mention",
    )
    _strip_at_mention.__doc__ = getattr(original, "__doc__", None)
    _strip_at_mention._qqbot_emoji_only_wrapped = True
    QQAdapter._strip_at_mention = staticmethod(_strip_at_mention)
    logger.info("qqbot-connect-hotfix: patched QQAdapter emoji-only group mentions")


def describe_qq_face_only_message(content: str) -> str:
    stripped = content.strip()
    if not stripped:
        return stripped

    face_tags = re.findall(r"<faceType=1,faceId=\"[^\"]+\"[^>]*>", stripped)
    if not face_tags:
        return stripped

    without_faces = re.sub(r"<faceType=1,faceId=\"[^\"]+\"[^>]*>", "", stripped)
    if without_faces.strip():
        return stripped

    count_text = "1 个" if len(face_tags) == 1 else f"{len(face_tags)} 个"
    return f"用户在群里 @ 了你，并发送了 {count_text} QQ 表情。请根据上下文做简短回应。"
