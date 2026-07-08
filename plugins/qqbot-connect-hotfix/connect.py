"""QQAdapter connection compatibility patch."""

from __future__ import annotations

import inspect
import logging

logger = logging.getLogger(__name__)


def patch_connect_signature(QQAdapter):
    original = QQAdapter.connect
    try:
        if "is_reconnect" in inspect.signature(original).parameters:
            logger.info("qqbot-connect-hotfix: QQAdapter.connect already accepts is_reconnect")
            return
    except (TypeError, ValueError) as exc:
        logger.debug("qqbot-connect-hotfix: could not inspect QQAdapter.connect: %s", exc)

    if getattr(original, "_qqbot_connect_hotfix_wrapped", False):
        return

    async def connect(self, *, is_reconnect: bool = False):
        return await original(self)

    connect.__name__ = getattr(original, "__name__", "connect")
    connect.__qualname__ = getattr(original, "__qualname__", "QQAdapter.connect")
    connect.__doc__ = getattr(original, "__doc__", None)
    connect._qqbot_connect_hotfix_wrapped = True
    QQAdapter.connect = connect
    logger.info("qqbot-connect-hotfix: patched QQAdapter.connect to accept is_reconnect")
