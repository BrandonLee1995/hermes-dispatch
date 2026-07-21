"""Persistent Codex app-server compatibility patches."""

from __future__ import annotations

import logging

from .phase_filter import patch_codex_app_server_event_bridge
from .image_delivery import patch_codex_image_delivery
from .approval_bridge import patch_codex_gateway_approvals

logger = logging.getLogger(__name__)


def register(ctx):
    """Install Codex app-server gateway compatibility patches."""
    phase_status = patch_codex_app_server_event_bridge()
    image_status = patch_codex_image_delivery()
    approval_status = patch_codex_gateway_approvals()
    logger.info("codex-app-server-phase-hotfix: %s", phase_status)
    logger.info("codex-app-server-phase-hotfix: image delivery %s", image_status)
    logger.info("codex-app-server-phase-hotfix: approvals %s", approval_status)
