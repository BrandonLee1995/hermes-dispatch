"""Local QQBot adapter compatibility patches."""

from __future__ import annotations

import logging

from .channel_directory import (
    channel_directory_paths as _channel_directory_paths,
    lookup_channel_directory_type as _lookup_channel_directory_type,
    patch_channel_directory_chat_type as _patch_channel_directory_chat_type,
)
from .connect import patch_connect_signature as _patch_connect_signature
from .emoji import (
    describe_qq_face_only_message as _describe_qq_face_only_message,
    patch_emoji_only_group_mentions as _patch_emoji_only_group_mentions,
)
from .group_context import (
    contains_mention_marker as _contains_mention_marker,
    group_context_block as _group_context_block,
    group_context_limit as _group_context_limit,
    patch_group_channel_context as _patch_group_channel_context,
    patch_group_message_create_event as _patch_group_message_create_event,
    remember_group_message as _remember_group_message,
    should_handle_group_message_create as _should_handle_group_message_create,
)
from .group_config_interaction import (
    patch_group_config_interactions as _patch_group_config_interactions,
)
from .outbound import (
    patch_media_caption_retry as _patch_media_caption_retry,
    patch_plain_text_retry as _patch_plain_text_retry,
    send_plain_text as _send_plain_text,
    should_retry_plain_text as _should_retry_plain_text,
)
from .approval_owner import (
    patch_shared_group_approval_owners as _patch_shared_group_approval_owners,
    patch_shared_group_typed_approvals as _patch_shared_group_typed_approvals,
)

logger = logging.getLogger(__name__)


def register(ctx):
    try:
        from gateway.platforms.qqbot.adapter import QQAdapter
    except ImportError as exc:
        logger.warning("qqbot-connect-hotfix: could not import QQAdapter: %s", exc)
        return

    _patch_connect_signature(QQAdapter)
    _patch_group_config_interactions(QQAdapter)
    _patch_channel_directory_chat_type(QQAdapter)
    _patch_emoji_only_group_mentions(QQAdapter)
    _patch_group_message_create_event(QQAdapter)
    _patch_group_channel_context(QQAdapter)
    _patch_plain_text_retry(QQAdapter)
    _patch_media_caption_retry(QQAdapter)
    approval_status = _patch_shared_group_approval_owners(QQAdapter)
    logger.info("qqbot-connect-hotfix: %s", approval_status)
    try:
        from gateway.slash_commands import GatewaySlashCommandsMixin

        typed_status = _patch_shared_group_typed_approvals(
            GatewaySlashCommandsMixin
        )
        logger.info("qqbot-connect-hotfix: %s", typed_status)
    except ImportError as exc:
        logger.warning(
            "qqbot-connect-hotfix: could not patch typed approvals: %s",
            exc,
        )
