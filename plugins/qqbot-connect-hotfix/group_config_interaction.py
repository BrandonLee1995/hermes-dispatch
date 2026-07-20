"""Handle QQ group-robot configuration interactions.

Tencent's current QQ connector uses ``INTERACTION_CREATE`` data types 2001
(configuration query) and 2002 (configuration update).  The ACK must include a
``claw_cfg`` object. Hermes 0.18.2 ACKs every interaction with only
``{"code": 0}``, so QQ cannot complete the group robot receive-mode handshake.

This patch mirrors that narrow protocol contract. QQ may deliver all group
messages (``require_mention=always``), while ``group_context.py`` independently
keeps Hermes' default response gate at mention-only. Thus passive messages can
be snapshotted and used as context without making the bot answer every line.
"""

from __future__ import annotations

import functools
import logging
import os
from typing import Any, Callable

logger = logging.getLogger(__name__)

_CONFIG_QUERY = 2001
_CONFIG_UPDATE = 2002
_ACK_MARKER = "_qqbot_config_interaction_ack_wrapped"
_HANDLER_MARKER = "_qqbot_config_interaction_handler_wrapped"


def _default_receive_mode() -> str:
    value = os.getenv("QQBOT_GROUP_RECEIVE_MODE", "all").strip().lower()
    return "mention" if value in {"mention", "at", "mentions", "off", "false", "0"} else "always"


def _interaction_mode(adapter: Any, raw: dict[str, Any]) -> str:
    group_openid = str(raw.get("group_openid") or "")
    modes = getattr(adapter, "_qqbot_group_receive_modes", None)
    if not isinstance(modes, dict):
        modes = {}
        adapter._qqbot_group_receive_modes = modes

    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    resolved = data.get("resolved") if isinstance(data.get("resolved"), dict) else {}
    update = resolved.get("claw_cfg") if isinstance(resolved.get("claw_cfg"), dict) else {}
    requested = str(update.get("require_mention") or "").strip().lower()
    if requested in {"always", "mention"} and group_openid:
        modes[group_openid] = requested
    return str(modes.get(group_openid) or _default_receive_mode())


def _claw_cfg(mode: str) -> dict[str, Any]:
    return {
        "channel_type": "qqbot",
        "channel_ver": "hermes-hotfix-1.5.1",
        # QQ's configuration-interaction contract currently identifies this
        # connector family as ``openclaw``. Keep that wire value even though
        # the runtime behind it is Hermes; arbitrary values are not documented
        # as accepted by the QQ client.
        "claw_type": "openclaw",
        "claw_ver": "0.18.2",
        "require_mention": mode,
        "group_policy": "open",
        "mention_patterns": "",
        "online_state": "online",
    }


def wrap_acknowledge_interaction(original: Callable) -> Callable:
    @functools.wraps(original)
    async def acknowledge(self, interaction_id: str, code: int = 0, data: Any = None):
        if data is None:
            return await original(self, interaction_id, code)
        if not getattr(self, "_http_client", None):
            raise RuntimeError("HTTP client not initialized — not connected?")
        from gateway.platforms.qqbot.adapter import API_BASE, DEFAULT_API_TIMEOUT
        from gateway.platforms.qqbot.utils import build_user_agent

        token = await self._ensure_token()
        response = await self._http_client.put(
            f"{API_BASE}/interactions/{interaction_id}",
            headers={
                "Authorization": f"QQBot {token}",
                "Content-Type": "application/json",
                "User-Agent": build_user_agent(),
            },
            json={"code": code, "data": data},
            timeout=DEFAULT_API_TIMEOUT,
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"Interaction ACK failed [{response.status_code}]: "
                f"{response.text[:200]}"
            )

    setattr(acknowledge, _ACK_MARKER, True)
    return acknowledge


def wrap_config_interactions(original: Callable) -> Callable:
    @functools.wraps(original)
    async def on_interaction(self, raw: Any):
        if not isinstance(raw, dict):
            return await original(self, raw)
        data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
        try:
            interaction_type = int(data.get("type") or 0)
        except (TypeError, ValueError):
            interaction_type = 0
        if interaction_type not in {_CONFIG_QUERY, _CONFIG_UPDATE}:
            return await original(self, raw)

        interaction_id = str(raw.get("id") or "")
        if not interaction_id:
            logger.warning("qqbot-connect-hotfix: config interaction missing id")
            return
        mode = _interaction_mode(self, raw)
        await self._acknowledge_interaction(
            interaction_id,
            0,
            {"claw_cfg": _claw_cfg(mode)},
        )
        logger.info(
            "qqbot-connect-hotfix: ACKed QQ group config interaction type=%d mode=%s",
            interaction_type,
            mode,
        )

    setattr(on_interaction, _HANDLER_MARKER, True)
    return on_interaction


def patch_group_config_interactions(QQAdapter) -> None:
    acknowledge = QQAdapter._acknowledge_interaction
    if not getattr(acknowledge, _ACK_MARKER, False):
        QQAdapter._acknowledge_interaction = wrap_acknowledge_interaction(acknowledge)

    handler = QQAdapter._on_interaction
    if not getattr(handler, _HANDLER_MARKER, False):
        QQAdapter._on_interaction = wrap_config_interactions(handler)
