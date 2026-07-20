"""Persistent message snapshots and hybrid retrieval for Hermes gateways."""

from __future__ import annotations

import logging

from .capture import patch_qq_snapshot_capture
from .config import SnapshotConfig
from .store import SnapshotStore
from .tools import register_snapshot_interfaces

logger = logging.getLogger(__name__)

_store: SnapshotStore | None = None


def register(ctx) -> None:
    """Initialize the durable store and register capture/retrieval interfaces."""
    global _store
    config = SnapshotConfig.from_env()
    _store = SnapshotStore(config)
    _store.initialize()
    register_snapshot_interfaces(ctx, _store)

    try:
        from gateway.platforms.qqbot.adapter import QQAdapter
    except ImportError as exc:
        logger.warning("message-snapshot-store: QQAdapter unavailable: %s", exc)
    else:
        patch_qq_snapshot_capture(QQAdapter, _store)

    logger.info(
        "message-snapshot-store: ready db=%s media=%s storage=%s context=%d/%d tokens",
        config.db_path,
        config.media_dir,
        config.media_storage,
        config.context_messages,
        config.context_tokens,
    )
