from __future__ import annotations

import logging

from .adapter_patch import patch_whatsapp_adapter
from .bridge_runtime import BridgePatchError, install_runtime_bridge
from .dashboard_env import patch_dashboard_env_metadata
from .slash_admin import patch_slash_access_env_policy

logger = logging.getLogger(__name__)


def register(ctx) -> None:
    patch_dashboard_env_metadata()
    patch_slash_access_env_policy()

    runtime_bridge = None
    try:
        runtime_bridge = install_runtime_bridge()
    except BridgePatchError as exc:
        logger.warning(
            "whatsapp-bridge-policy-hotfix: bridge runtime not installed: %s",
            exc,
        )

    try:
        patch_whatsapp_adapter(runtime_bridge)
    except ImportError as exc:
        logger.warning(
            "whatsapp-bridge-policy-hotfix: could not import WhatsAppAdapter: %s",
            exc,
        )
