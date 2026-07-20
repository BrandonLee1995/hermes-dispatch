"""Persistent Codex app-server compatibility patches."""

from __future__ import annotations

import logging

from .phase_filter import patch_codex_app_server_event_bridge
from .image_delivery import patch_codex_image_delivery

logger = logging.getLogger(__name__)


def register(ctx):
    """Install phase and generated-image delivery compatibility patches."""
    phase_status = patch_codex_app_server_event_bridge()
    image_status = patch_codex_image_delivery()
    logger.info("codex-app-server-phase-hotfix: %s", phase_status)
    logger.info("codex-app-server-phase-hotfix: image delivery %s", image_status)
