"""Native QQ C2C streaming compatibility for Hermes gateways.

QQ's ``/v2/users/{openid}/stream_messages`` endpoint treats the stream as
the message: the first frame opens it, continuation frames replace the
visible body, and ``input_state=10`` seals the same message.  Hermes' generic
draft consumer can drive that lifecycle when the adapter advertises native
draft support and converts the turn-final ``send()`` into the sealing frame.

This module intentionally patches only C2C chats.  QQ group messages use a
different passive-reply contract and do not expose the C2C stream endpoint.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_STREAM_PATCHED = "_qqbot_native_c2c_streaming_patched"
_RUNNER_PATCHED = "_qqbot_native_c2c_streaming_runner_patched"
_OVERFLOW_PATCHED = "_qqbot_native_c2c_overflow_patched"
_MIN_HERMES_VERSION = (0, 20, 5)
_MAX_OPEN_STREAMS = 128
_MAX_COMPLETED_OWNERS_PER_CHAT = 256
_MAX_COMPLETED_OWNER_CHATS = 1024
_MAX_FINAL_ONLY_PENDING = 256
_MAX_FINAL_ONLY_PENDING_CHATS = 1024
_MAX_NATIVE_LANE_CHATS = 1024
_MAX_TYPING_ANCHORS = 1024
_NATIVE_STREAM_ACCUMULATION_LIMIT = 2**31 - 1
_SEAL_RETRY_DELAYS = (0.0, 0.2, 0.8)


def _hermes_version_tuple() -> tuple[int, ...]:
    """Return the running Hermes version without consulting package metadata.

    Profile installs can place a newer distribution metadata record beside an
    older source checkout.  ``hermes_cli.__version__`` follows the code that is
    actually imported by the Gateway, which is the compatibility boundary that
    matters here.
    """

    try:
        from hermes_cli import __version__
    except Exception:
        return ()
    match = re.fullmatch(r"\s*(\d+)\.(\d+)\.(\d+)\s*", str(__version__))
    if match is None:
        # Pre-releases (rc/dev/alpha/beta), local suffixes, and unknown version
        # shapes fail closed. The streaming contract is guaranteed only by a
        # stable Hermes release at or above the minimum.
        return ()
    return tuple(int(part) for part in match.groups())


def _hermes_streaming_supported() -> bool:
    version = _hermes_version_tuple()
    return bool(version) and version >= _MIN_HERMES_VERSION


def _send_result(*, success: bool, message_id=None, error=None, raw_response=None):
    from gateway.platforms.base import SendResult

    return SendResult(
        success=success,
        message_id=message_id,
        error=error,
        raw_response=raw_response,
        retryable=not success,
    )


@dataclass
class _QQC2CStream:
    chat_id: str
    draft_id: int
    reply_to: str
    msg_seq: int
    stream_msg_id: Optional[str] = None
    next_index: int = 0
    last_content: str = ""
    committed_prefix: str = ""
    last_completed_stream_id: Optional[str] = None
    # Text successfully delivered by the immutable ordinary-message fallback.
    # It is already user-visible but can never be absorbed into a later native
    # replace/seal without displaying the same suffix twice.
    ordinary_owned_suffix: str = ""
    sealed: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass(frozen=True)
class _QQC2CTurnTombstone:
    """Bounded lifecycle evidence for a turn removed from the active maps."""

    chat_id: str
    draft_id: int
    reply_to: str
    final_payload: str
    final_content: str
    message_id: Optional[str] = None
    final_delivered: bool = True


def _stream_maps(adapter):
    streams = getattr(adapter, "_qq_native_c2c_streams", None)
    if streams is None:
        streams = {}
        adapter._qq_native_c2c_streams = streams
    anchors = getattr(adapter, "_qq_native_c2c_streams_by_anchor", None)
    if anchors is None:
        anchors = {}
        adapter._qq_native_c2c_streams_by_anchor = anchors
    return streams, anchors


def _stream_key(chat_id: str, draft_id: int) -> tuple[str, int]:
    """Return the adapter-contract identity for one active draft."""

    return str(chat_id), int(draft_id)


def _turn_tombstones(
    adapter,
) -> Dict[str, Dict[tuple[str, int], _QQC2CTurnTombstone]]:
    owners = getattr(adapter, "_qq_native_c2c_completed_owners", None)
    if owners is None:
        owners = {}
        adapter._qq_native_c2c_completed_owners = owners
    return owners


def _remember_turn_tombstone(
    adapter,
    state: _QQC2CStream,
    *,
    final_payload: str,
    final_content: str,
    final_delivered: bool = True,
) -> None:
    """Retain exact turn lifecycle evidence without growing indefinitely."""

    owners = _turn_tombstones(adapter)
    bucket = owners.pop(state.chat_id, None)
    if bucket is None:
        bucket = {}
    owners[state.chat_id] = bucket
    key = (state.reply_to, state.draft_id)
    bucket.pop(key, None)
    bucket[key] = _QQC2CTurnTombstone(
        chat_id=state.chat_id,
        draft_id=state.draft_id,
        reply_to=state.reply_to,
        final_payload=str(final_payload or ""),
        final_content=str(final_content or ""),
        message_id=state.stream_msg_id or state.last_completed_stream_id,
        final_delivered=final_delivered,
    )
    while len(bucket) > _MAX_COMPLETED_OWNERS_PER_CHAT:
        bucket.pop(next(iter(bucket)))
    while len(owners) > _MAX_COMPLETED_OWNER_CHATS:
        owners.pop(next(iter(owners)))


def _completed_owner_for_draft(
    adapter,
    *,
    chat_id: str,
    reply_to: str,
    draft_id: int,
) -> Optional[_QQC2CTurnTombstone]:
    owners = _turn_tombstones(adapter)
    chat_key = str(chat_id)
    bucket = owners.get(chat_key, {})
    owner = bucket.get((str(reply_to), int(draft_id)))
    if owner is not None:
        owners.pop(chat_key, None)
        owners[chat_key] = bucket
    return owner


def _completed_owner_for_final(
    adapter,
    *,
    chat_id: str,
    reply_to: str,
    content: str,
) -> Optional[_QQC2CTurnTombstone]:
    payload = str(content or "")
    owners = _turn_tombstones(adapter)
    chat_key = str(chat_id)
    bucket = owners.get(chat_key, {})
    for owner in reversed(tuple(bucket.values())):
        if (
            owner.final_delivered
            and owner.chat_id == str(chat_id)
            and owner.reply_to == str(reply_to)
            and payload in (owner.final_payload, owner.final_content)
        ):
            owners.pop(chat_key, None)
            owners[chat_key] = bucket
            return owner
    return None


def _cancelled_owner_for_anchor(
    adapter,
    *,
    chat_id: str,
    reply_to: str,
) -> Optional[_QQC2CTurnTombstone]:
    """Return cancellation evidence that still needs one visible final."""

    owners = _turn_tombstones(adapter)
    chat_key = str(chat_id)
    bucket = owners.get(chat_key, {})
    for owner in reversed(tuple(bucket.values())):
        if (
            not owner.final_delivered
            and owner.chat_id == chat_key
            and owner.reply_to == str(reply_to)
        ):
            owners.pop(chat_key, None)
            owners[chat_key] = bucket
            return owner
    return None


def _promote_cancelled_owner(
    adapter,
    owner: _QQC2CTurnTombstone,
    *,
    final_content: str,
    message_id: Optional[str],
) -> None:
    """Record that a formerly cancelled turn now owns one delivered final."""

    owners = _turn_tombstones(adapter)
    bucket = owners.get(owner.chat_id, {})
    key = (owner.reply_to, owner.draft_id)
    if bucket.get(key) != owner:
        return
    payload = str(final_content or "")
    bucket[key] = _QQC2CTurnTombstone(
        chat_id=owner.chat_id,
        draft_id=owner.draft_id,
        reply_to=owner.reply_to,
        final_payload=payload,
        final_content=payload,
        message_id=message_id,
        final_delivered=True,
    )
    owners.pop(owner.chat_id, None)
    owners[owner.chat_id] = bucket


def _final_only_pending(adapter) -> Dict[str, Dict[tuple[str, int], _QQC2CStream]]:
    pending = getattr(adapter, "_qq_native_c2c_final_only_pending", None)
    if pending is None:
        pending = {}
        adapter._qq_native_c2c_final_only_pending = pending
    return pending


def _remember_final_only_pending(adapter, state: _QQC2CStream) -> None:
    pending = _final_only_pending(adapter)
    bucket = pending.pop(state.chat_id, None)
    if bucket is None:
        bucket = {}
    pending[state.chat_id] = bucket
    key = (state.reply_to, state.draft_id)
    bucket.pop(key, None)
    bucket[key] = state
    while len(bucket) > _MAX_FINAL_ONLY_PENDING:
        bucket.pop(next(iter(bucket)))
    while len(pending) > _MAX_FINAL_ONLY_PENDING_CHATS:
        pending.pop(next(iter(pending)))


def _final_only_pending_for_draft(
    adapter,
    *,
    chat_id: str,
    reply_to: str,
    draft_id: int,
) -> Optional[_QQC2CStream]:
    pending = _final_only_pending(adapter)
    chat_key = str(chat_id)
    bucket = pending.get(chat_key, {})
    state = bucket.get((str(reply_to), int(draft_id)))
    if state is not None:
        pending.pop(chat_key, None)
        pending[chat_key] = bucket
    return state


def _final_only_pending_for_anchor(
    adapter,
    *,
    chat_id: str,
    reply_to: str,
) -> Optional[_QQC2CStream]:
    pending = _final_only_pending(adapter)
    chat_key = str(chat_id)
    bucket = pending.get(chat_key, {})
    for (owner_reply_to, _draft_id), state in reversed(tuple(bucket.items())):
        if owner_reply_to == str(reply_to):
            pending.pop(chat_key, None)
            pending[chat_key] = bucket
            return state
    return None


def _remove_final_only_pending(adapter, state: _QQC2CStream) -> None:
    pending = _final_only_pending(adapter)
    bucket = pending.get(state.chat_id)
    if bucket is None:
        return
    bucket.pop((state.reply_to, state.draft_id), None)
    if not bucket:
        pending.pop(state.chat_id, None)


def _remove_stream(adapter, state: _QQC2CStream) -> None:
    streams, anchors = _stream_maps(adapter)
    state_key = _stream_key(state.chat_id, state.draft_id)
    streams.pop(state_key, None)
    anchor_key = (state.chat_id, state.reply_to)
    if anchors.get(anchor_key) == state_key:
        anchors.pop(anchor_key, None)
    _prune_native_lane_chats(adapter)


def _evict_unopened_streams(adapter, *, limit: int) -> None:
    """Reclaim only streams that never became visible on QQ.

    An opened stream must remain addressable until it is sealed.  Silently
    dropping its local state can strand a client-visible message in the
    generating state.  If all slots are opened, the new turn stays final-only
    instead of sacrificing an existing stream.
    """

    streams, anchors = _stream_maps(adapter)
    while len(streams) > max(0, limit):
        removable = next(
            (
                state
                for state in streams.values()
                if not state.stream_msg_id and not state.lock.locked()
            ),
            None,
        )
        if removable is None:
            break
        _remove_stream(adapter, removable)
    # Defensive cleanup for anchors whose stream was removed independently.
    for anchor_key, draft_id in list(anchors.items()):
        if draft_id not in streams:
            anchors.pop(anchor_key, None)


def _native_lane_chats(adapter) -> Dict[str, None]:
    chats = getattr(adapter, "_qq_native_c2c_lane_chats", None)
    if chats is None:
        chats = {}
        adapter._qq_native_c2c_lane_chats = chats
    elif isinstance(chats, set):
        # Accept an adapter created by an older in-process patch while
        # upgrading the lifetime-wide set to the bounded LRU representation.
        chats = dict.fromkeys(str(chat_id) for chat_id in chats)
        adapter._qq_native_c2c_lane_chats = chats
    _prune_native_lane_chats(adapter, chats)
    return chats


def _prune_native_lane_chats(
    adapter,
    chats: Optional[Dict[str, None]] = None,
) -> None:
    if chats is None:
        chats = _native_lane_chats(adapter)
    streams, _anchors = _stream_maps(adapter)
    active_chats = {state.chat_id for state in streams.values()}
    while len(chats) > max(0, _MAX_NATIVE_LANE_CHATS):
        removable = next(
            (chat_id for chat_id in chats if chat_id not in active_chats),
            None,
        )
        if removable is None:
            # Open native streams must stay selectable until sealed. The
            # registry converges as soon as one is removed.
            break
        chats.pop(removable, None)


def _mark_native_lane(adapter, chat_id: str) -> None:
    if chat_id:
        chats = _native_lane_chats(adapter)
        chat_key = str(chat_id)
        chats.pop(chat_key, None)
        chats[chat_key] = None
        _prune_native_lane_chats(adapter, chats)


def _unmark_native_lane(adapter, chat_id: str) -> None:
    if chat_id:
        chats = _native_lane_chats(adapter)
        chats.pop(str(chat_id), None)
        _prune_native_lane_chats(adapter, chats)


def _typing_budget_applies(adapter, chat_id: str) -> bool:
    chat_id = str(chat_id)
    if chat_id in _native_lane_chats(adapter):
        return True
    streams, _anchors = _stream_maps(adapter)
    return any(state.chat_id == chat_id for state in streams.values())


def _resolved_platform_streaming_enabled(source, scfg) -> bool:
    """Mirror Hermes' global + per-platform streaming resolution.

    Hermes can create a ``GatewayStreamConsumer`` solely for interim assistant
    messages even when streaming itself is disabled. The native QQ lane must
    therefore use the already-resolved display setting, not consumer creation
    as evidence that streaming was enabled.
    """

    global_enabled = bool(
        getattr(scfg, "enabled", False)
        and str(getattr(scfg, "transport", "auto") or "auto").lower() != "off"
    )
    try:
        from gateway.display_config import resolve_display_setting
        from gateway.run import _load_gateway_config, _platform_config_key

        platform_key = _platform_config_key(getattr(source, "platform", "qqbot"))
        override = resolve_display_setting(
            _load_gateway_config(),
            platform_key,
            "streaming",
        )
    except Exception:
        logger.debug(
            "qqbot-connect-hotfix: could not resolve per-platform streaming; "
            "native QQ streaming stays disabled",
            exc_info=True,
        )
        return False
    return global_enabled if override is None else bool(override)


def _patch_gateway_stream_gate(QQAdapter) -> str:
    """Let native QQ C2C drafts pass Hermes' legacy edit-only gate.

    Hermes currently rejects every non-editable adapter before
    ``GatewayStreamConsumer`` can ask whether it supports a native draft
    transport. QQ cannot edit ordinary messages, but its C2C stream endpoint
    is exactly such a native transport. Narrow the exception to QQ C2C only;
    groups and every other non-editable platform keep the upstream guard.
    """

    try:
        from gateway.run import GatewayRunner
    except ImportError as exc:
        logger.warning(
            "qqbot-connect-hotfix: could not patch Gateway streaming gate: %s",
            exc,
        )
        return "QQ C2C Gateway streaming gate unavailable"

    original_build = GatewayRunner._build_stream_consumer_config
    if getattr(original_build, _RUNNER_PATCHED, False):
        return "QQ C2C Gateway streaming gate already patched"

    @functools.wraps(original_build)
    def _build_stream_consumer_config(
        self,
        source,
        scfg,
        adapter,
        *,
        on_missing_cursor: str,
    ):
        if isinstance(adapter, QQAdapter):
            chat_id = str(getattr(source, "chat_id", "") or "")
            native_c2c = (
                _resolved_platform_streaming_enabled(source, scfg)
                and _is_c2c(
                    adapter,
                    chat_id,
                    getattr(source, "chat_type", "") or None,
                )
            )
            if native_c2c:
                _mark_native_lane(adapter, chat_id)
            else:
                # Config is resolved for every new turn. Revoke a lane that
                # was selected before a live enabled -> disabled transition;
                # any already-open stream remains protected by _stream_maps.
                _unmark_native_lane(adapter, chat_id)
            if (
                native_c2c
                and on_missing_cursor == "raise"
                and not getattr(adapter, "SUPPORTS_MESSAGE_EDITING", True)
            ):
                config, pause_typing = original_build(
                    self,
                    source,
                    scfg,
                    adapter,
                    on_missing_cursor="fallback",
                )
                # QQ renders its own native generating state. A text cursor
                # is unnecessary and would break replace-prefix stability.
                config.cursor = ""
                return config, pause_typing

        return original_build(
            self,
            source,
            scfg,
            adapter,
            on_missing_cursor=on_missing_cursor,
        )

    setattr(_build_stream_consumer_config, _RUNNER_PATCHED, True)
    GatewayRunner._build_stream_consumer_config = _build_stream_consumer_config
    return "QQ C2C Gateway streaming gate patched"


def _patch_gateway_overflow_limit(QQAdapter) -> str:
    """Keep QQ C2C cumulative text intact for adapter-owned rollover.

    Hermes' generic overflow path seals a head as an ordinary message and
    resets its accumulator to the tail. QQ native replace streams instead need
    each active stream to retain its own accepted prefix. Defer splitting only
    for an active QQ C2C native lane; the adapter then seals full stream chunks
    and opens a fresh stream for the remaining cumulative suffix.
    """

    try:
        from gateway.stream_consumer import GatewayStreamConsumer
    except ImportError as exc:
        logger.warning(
            "qqbot-connect-hotfix: could not patch native overflow limit: %s",
            exc,
        )
        return "QQ C2C native overflow patch unavailable"

    original_limit = GatewayStreamConsumer._raw_message_limit
    if getattr(original_limit, _OVERFLOW_PATCHED, False):
        return "QQ C2C native overflow already patched"

    @functools.wraps(original_limit)
    def _raw_message_limit(self):
        base = original_limit(self)
        adapter = getattr(self, "adapter", None)
        chat_id = str(getattr(self, "chat_id", "") or "")
        if (
            isinstance(adapter, QQAdapter)
            and chat_id in _native_lane_chats(adapter)
            and _is_c2c(adapter, chat_id)
        ):
            return max(int(base), _NATIVE_STREAM_ACCUMULATION_LIMIT)
        return base

    setattr(_raw_message_limit, _OVERFLOW_PATCHED, True)
    GatewayStreamConsumer._raw_message_limit = _raw_message_limit
    return "QQ C2C native overflow patched"


def _reply_anchor(metadata: Optional[Dict[str, Any]]) -> str:
    if not isinstance(metadata, dict):
        return ""
    return str(metadata.get("reply_to_message_id") or "").strip()


def _is_c2c(adapter, chat_id: str, chat_type: Optional[str] = None) -> bool:
    try:
        # QQ uses source.chat_type="dm" for both C2C and guild direct
        # messages. The adapter route map is the authoritative distinction:
        # only "c2c" supports /v2/users/{openid}/stream_messages.
        return str(adapter._guess_chat_type(chat_id)).lower() == "c2c"
    except Exception:
        # A literal c2c value remains a safe compatibility fallback for test
        # or relay adapters that do not expose QQ's route helper. Generic
        # dm/private values are intentionally insufficient.
        return str(chat_type or "").strip().lower() == "c2c"


def _stream_body(
    adapter,
    state: _QQC2CStream,
    content: str,
    *,
    input_state: int,
) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "input_mode": "replace",
        "input_state": input_state,
        "index": state.next_index,
        "content_type": (
            "markdown" if getattr(adapter, "_markdown_support", False) else "text"
        ),
        "content_raw": str(content or "")[: getattr(adapter, "MAX_MESSAGE_LENGTH", 4000)],
        "msg_id": state.reply_to,
        "msg_seq": state.msg_seq,
    }
    if state.stream_msg_id:
        body["stream_msg_id"] = state.stream_msg_id
    return body


async def _post_stream_frame(
    adapter,
    state: _QQC2CStream,
    content: str,
    *,
    input_state: int,
):
    body = _stream_body(adapter, state, content, input_state=input_state)
    data = await adapter._api_request(
        "POST",
        f"/v2/users/{state.chat_id}/stream_messages",
        body,
    )
    response_id = str((data or {}).get("id") or "").strip()
    if state.next_index == 0 and not response_id:
        raise RuntimeError("QQ stream first frame did not return stream_msg_id")
    if response_id:
        state.stream_msg_id = response_id
    if state.next_index == 0:
        logger.info(
            "qqbot-connect-hotfix: QQ C2C stream opened draft=%s",
            state.draft_id,
        )
    else:
        logger.debug(
            "qqbot-connect-hotfix: QQ C2C stream frame draft=%s index=%s",
            state.draft_id,
            state.next_index,
        )
    state.next_index += 1
    # Track the exact body submitted to QQ, including the platform length cap.
    # A later replace/seal request must preserve this already-submitted prefix.
    state.last_content = str(body["content_raw"])
    return data


def _active_content(
    state: _QQC2CStream,
    content: str,
    *,
    require_committed_prefix: bool,
) -> str:
    """Return the portion belonging to the currently open stream chunk."""

    full = str(content or "")
    committed = str(state.committed_prefix or "")
    if not committed:
        return full
    if full.startswith(committed):
        return full[len(committed):]
    if require_committed_prefix:
        raise RuntimeError(
            "QQ cumulative draft no longer preserves its sealed overflow prefix"
        )
    # Turn-final text can legitimately contain only the final assistant answer
    # while commentary was already streamed. Let _seal_content append that
    # authoritative suffix to the current visible chunk.
    return full


def _visible_stream_content(state: _QQC2CStream) -> str:
    """Return text that QQ has acknowledged as client-visible exactly once."""

    current = str(state.last_content or "") if state.stream_msg_id else ""
    return str(state.committed_prefix or "") + current


def _terminal_payload_is_owned(base: str, payload: str) -> bool:
    """Return whether *payload* has an explicit terminal owner in *base*.

    This is deliberately stricter than substring/overlap matching.  It is used
    both for final composition and for Hermes' ``_interim_send`` callback that
    follows a completed Codex commentary item.  In the latter path the live
    token deltas may already have placed the exact commentary at the end of the
    QQ native stream; sending the callback again would create a second ordinary
    message bubble.
    """

    base = str(base or "")
    payload = str(payload or "")
    if not base or not payload or not base.endswith(payload):
        return False
    boundary = len(base) - len(payload)
    if boundary == 0:
        return True
    previous = base[boundary - 1]
    category = unicodedata.category(previous)
    return bool(
        previous.isspace()
        or (category.startswith("P") and category != "Pc")
    )


def _append_nonoverlapping(base: str, suffix_source: str) -> str:
    """Compose a cumulative or independent final without value guessing.

    Hermes may provide either the complete cumulative response or a separate
    authoritative final after streamed commentary. Only two observations are
    safe ownership evidence:

    * the final explicitly extends the complete visible body; or
    * the exact final payload is already the terminal, token-bounded body.

    An occurrence elsewhere in commentary, or a coincidental suffix/prefix
    overlap, does not own an independent final and must not swallow it.
    """

    base = str(base or "")
    suffix_source = str(suffix_source or "")
    if not base:
        return suffix_source
    if not suffix_source or suffix_source == base:
        return base
    if suffix_source.startswith(base):
        return suffix_source

    if _terminal_payload_is_owned(base, suffix_source):
        return base

    separator = (
        ""
        if base.endswith(("\n", " ", "\t"))
        or suffix_source.startswith(("\n", " ", "\t"))
        else "\n"
    )
    return base + separator + suffix_source


def _compose_final_content(state: _QQC2CStream, content: str) -> str:
    """Build the lossless final text from visible drafts and Hermes' final."""

    return _append_nonoverlapping(_visible_stream_content(state), str(content or ""))


def _unseen_final_suffix(state: _QQC2CStream, target: str) -> Optional[str]:
    """Return the target suffix not yet visible, or ``None`` on invariant loss."""

    visible = _visible_stream_content(state)
    if not str(target or "").startswith(visible):
        return None
    return str(target or "")[len(visible):]


async def _post_seal_with_retries(
    adapter,
    state: _QQC2CStream,
    content: str,
):
    data = None
    last_error = None
    for attempt, delay in enumerate(_SEAL_RETRY_DELAYS, start=1):
        if delay:
            await asyncio.sleep(delay)
        try:
            data = await _post_stream_frame(
                adapter,
                state,
                content,
                input_state=10,
            )
            last_error = None
            break
        except Exception as exc:
            last_error = exc
            logger.warning(
                "qqbot-connect-hotfix: QQ C2C stream seal attempt %s/%s "
                "failed for chat=%s draft=%s index=%s: %s",
                attempt,
                len(_SEAL_RETRY_DELAYS),
                state.chat_id,
                state.draft_id,
                state.next_index,
                exc,
            )
    return data, last_error


def _replace_active_stream(adapter, state: _QQC2CStream) -> None:
    streams, anchors = _stream_maps(adapter)
    state_key = _stream_key(state.chat_id, state.draft_id)
    streams[state_key] = state
    anchors[(state.chat_id, state.reply_to)] = state_key


async def _send_cumulative_draft(adapter, state: _QQC2CStream, content: str):
    """Send a cumulative frame, rolling full chunks into new QQ streams.

    Every stream independently obeys QQ's replace-prefix rule. Once the active
    suffix exceeds the per-message cap, its full head is sealed and recorded
    as ``committed_prefix``; a new stream then owns only the remaining suffix.
    """

    active = _active_content(
        state,
        content,
        require_committed_prefix=True,
    )
    max_length = int(getattr(adapter, "MAX_MESSAGE_LENGTH", 4000))
    if max_length <= 0:
        raise RuntimeError("QQ native stream message limit must be positive")

    current = state
    data = None
    while len(active) > max_length:
        head = active[:max_length]
        if not current.stream_msg_id:
            data = await _post_stream_frame(
                adapter,
                current,
                head,
                input_state=1,
            )
        data, seal_error = await _post_seal_with_retries(
            adapter,
            current,
            head,
        )
        if seal_error is not None:
            raise seal_error

        current.sealed = True
        committed = current.committed_prefix + head
        completed_id = current.stream_msg_id or current.last_completed_stream_id
        logger.info(
            "qqbot-connect-hotfix: QQ C2C overflow chunk sealed "
            "draft=%s committed=%s",
            current.draft_id,
            len(committed),
        )
        current = _QQC2CStream(
            chat_id=current.chat_id,
            draft_id=current.draft_id,
            reply_to=current.reply_to,
            msg_seq=int(adapter._next_msg_seq(current.reply_to)),
            committed_prefix=committed,
            last_completed_stream_id=completed_id,
        )
        _replace_active_stream(adapter, current)
        active = active[max_length:]

    if active and (not current.stream_msg_id or active != current.last_content):
        data = await _post_stream_frame(
            adapter,
            current,
            active,
            input_state=1,
        )
    return current, data


def _seal_content(adapter, state: _QQC2CStream, content: str) -> tuple[str, str]:
    """Compose one legal seal body and return any overflow separately.

    Hermes' draft contains commentary, tool progress, and often the final
    answer, while its turn-final ``send`` can contain only the short final
    answer. QQ rejects a replace request that removes an already-submitted
    prefix. Never silently discard overflow: callers must roll it into another
    native stream or assign it to an ordinary fallback before reporting
    success.
    """

    previous = str(state.last_content or "")
    max_length = int(getattr(adapter, "MAX_MESSAGE_LENGTH", 4000))
    composed = _append_nonoverlapping(previous, str(content or ""))
    return composed[:max_length], composed[max_length:]


async def _seal_stream(adapter, state: _QQC2CStream, content: str):
    async with state.lock:
        if state.sealed:
            return _send_result(
                success=True,
                message_id=state.stream_msg_id,
            )
        if not state.stream_msg_id:
            return _send_result(
                success=False,
                error="QQ stream cannot be sealed before its first frame",
            )
        seal_content, overflow = _seal_content(adapter, state, content)
        if overflow:
            return _send_result(
                success=False,
                error=(
                    "QQ stream seal requires rollover before closing "
                    f"({len(overflow)} unassigned characters)"
                ),
            )
        data, last_error = await _post_seal_with_retries(
            adapter,
            state,
            seal_content,
        )
        if last_error is not None:
            # Keep both maps intact. A later turn-final retry or
            # abandon_open_draft can still seal this already-visible stream.
            return _send_result(success=False, error=str(last_error))

        state.sealed = True
        logger.info(
            "qqbot-connect-hotfix: QQ C2C stream sealed draft=%s frames=%s",
            state.draft_id,
            state.next_index,
        )
        _remove_stream(adapter, state)
        return _send_result(
            success=True,
            message_id=state.stream_msg_id,
            raw_response=data,
        )


def patch_qq_c2c_streaming(QQAdapter):
    """Add official QQ C2C native streaming to ``QQAdapter``.

    The patch composes with Hermes' ``GatewayStreamConsumer``:

    * ``send_draft`` opens/replaces the QQ stream;
    * ``draft_stream_is_message`` keeps one stream across tool boundaries;
    * the turn-final ``send`` seals it with ``input_state=10``;
    * cancellation uses ``abandon_open_draft`` to close the visible stream;
    * QQ's passive ``input_notify`` is emitted at most once per inbound
      ``msg_id`` so a fallback path still has reply budget for the final.
    """

    if not _hermes_streaming_supported():
        found = _hermes_version_tuple()
        found_text = ".".join(str(part) for part in found) or "unknown"
        return (
            "QQ C2C native streaming disabled: requires Hermes >=0.20.5 "
            f"(found {found_text})"
        )

    original_send = QQAdapter.send
    if getattr(original_send, _STREAM_PATCHED, False):
        return "QQ C2C native streaming already patched"

    original_send_typing = QQAdapter.send_typing

    def supports_draft_streaming(
        self,
        chat_type: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        chat_id: Optional[str] = None,
    ) -> bool:
        del metadata
        return bool(chat_id) and str(chat_id) in _native_lane_chats(self) and _is_c2c(
            self,
            str(chat_id),
            chat_type,
        )

    def stream_is_message_for_chat(self, chat_id: str) -> bool:
        return _is_c2c(self, str(chat_id))

    async def send_draft(
        self,
        chat_id: str,
        draft_id: int,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        chat_id = str(chat_id)
        if not _is_c2c(self, chat_id):
            return _send_result(
                success=False,
                error="QQ native streaming is supported only for C2C chats",
            )

        reply_to = _reply_anchor(metadata)
        if not reply_to:
            reply_to = str(getattr(self, "_last_msg_id", {}).get(chat_id) or "")
        if not reply_to:
            return _send_result(
                success=False,
                error="QQ native streaming requires the inbound reply msg_id",
            )

        streams, anchors = _stream_maps(self)
        state_key = _stream_key(chat_id, int(draft_id))
        state = streams.get(state_key)
        if state is None:
            completed_owner = _completed_owner_for_draft(
                self,
                chat_id=chat_id,
                reply_to=reply_to,
                draft_id=int(draft_id),
            )
            if completed_owner is not None:
                return _send_result(
                    success=True,
                    message_id=completed_owner.message_id,
                    raw_response={"qq_completed_turn_owned": True},
                )
            pending_state = _final_only_pending_for_draft(
                self,
                chat_id=chat_id,
                reply_to=reply_to,
                draft_id=int(draft_id),
            )
            if pending_state is not None:
                return _send_result(
                    success=True,
                    raw_response={"qq_final_only_pending": True},
                )
            _evict_unopened_streams(self, limit=_MAX_OPEN_STREAMS - 1)
            if len(streams) >= _MAX_OPEN_STREAMS:
                logger.warning(
                    "qqbot-connect-hotfix: native C2C stream capacity reached; "
                    "keeping %s opened streams retryable and using final-only "
                    "delivery for chat=%s",
                    len(streams),
                    chat_id,
                )
                # Reporting success keeps GatewayStreamConsumer on the native
                # lane without emitting an uneditable partial. Since no anchor
                # is registered, retain a separate bounded identity so the
                # turn-final wrapper can own one normal final and reject stale
                # retries/late draft frames after completion.
                _remember_final_only_pending(
                    self,
                    _QQC2CStream(
                        chat_id=chat_id,
                        draft_id=int(draft_id),
                        reply_to=reply_to,
                        msg_seq=int(self._next_msg_seq(reply_to)),
                    ),
                )
                return _send_result(success=True)
            state = _QQC2CStream(
                chat_id=chat_id,
                draft_id=int(draft_id),
                reply_to=reply_to,
                msg_seq=int(self._next_msg_seq(reply_to)),
            )
            streams[state_key] = state
            anchors[(chat_id, reply_to)] = state_key
            _mark_native_lane(self, chat_id)
        elif state.reply_to != reply_to:
            return _send_result(
                success=False,
                error="QQ native stream draft identity changed mid-turn",
            )

        async with state.lock:
            if state.sealed:
                return _send_result(
                    success=False,
                    error="QQ native stream is already sealed",
                )
            if state.ordinary_owned_suffix:
                # The turn already completed through an immutable ordinary
                # fallback. A late frame from the old consumer cannot safely
                # move that suffix back into the native replace lifecycle.
                return _send_result(
                    success=True,
                    message_id=state.stream_msg_id,
                    raw_response={"qq_ordinary_suffix_owned": True},
                )
            try:
                state, data = await _send_cumulative_draft(
                    self,
                    state,
                    content,
                )
            except Exception as exc:
                logger.warning(
                    "qqbot-connect-hotfix: QQ C2C stream frame deferred for "
                    "chat=%s draft=%s index=%s; retaining final-only "
                    "fallback: %s",
                    chat_id,
                    draft_id,
                    state.next_index,
                    exc,
                )
                # QQ ordinary messages cannot be edited. Reporting failure to
                # GatewayStreamConsumer would make its generic fallback send a
                # partial message immediately, then another final. Keep the
                # native lane selected: a later frame can retry the same index;
                # if no frame ever opens, the final send wrapper falls back to
                # exactly one ordinary message.
                return _send_result(success=True)

        return _send_result(
            success=True,
            raw_response=data,
        )

    async def abandon_open_draft(
        self,
        chat_id: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        anchor = _reply_anchor(metadata)
        streams, anchors = _stream_maps(self)
        state_key = anchors.get((str(chat_id), anchor))
        state = streams.get(state_key) if state_key is not None else None
        if state is None:
            pending_state = _final_only_pending_for_anchor(
                self,
                chat_id=str(chat_id),
                reply_to=anchor,
            )
            if pending_state is not None:
                _remember_turn_tombstone(
                    self,
                    pending_state,
                    final_payload=str(content or ""),
                    final_content=str(content or ""),
                    final_delivered=False,
                )
                _remove_final_only_pending(self, pending_state)
            return _send_result(success=True)
        if state.ordinary_owned_suffix:
            # A completed ordinary fallback already owns every character after
            # the native stream's last acknowledged frame. Delayed cancellation
            # cleanup must only close that native frame; the caller commonly
            # passes the full final payload here, which would otherwise absorb
            # and duplicate the immutable suffix.
            return await _seal_stream(
                self,
                state,
                state.last_content,
            )
        active_content = _active_content(
            state,
            content or state.last_content,
            require_committed_prefix=False,
        )
        if not state.stream_msg_id and state.committed_prefix and not active_content:
            completed_id = state.last_completed_stream_id
            _remember_turn_tombstone(
                self,
                state,
                final_payload=str(content or ""),
                final_content=state.committed_prefix,
            )
            _remove_stream(self, state)
            return _send_result(success=True, message_id=completed_id)
        sealed = await _seal_stream(
            self,
            state,
            active_content or state.last_content,
        )
        if sealed.success:
            _remember_turn_tombstone(
                self,
                state,
                final_payload=str(content or ""),
                final_content=_visible_stream_content(state),
            )
        return sealed

    @functools.wraps(original_send)
    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        # Hermes emits a completed Codex commentary item through an ordinary
        # ``_interim_send`` callback even when its live deltas already rendered
        # the same item in this turn's native QQ stream. Suppress only when the
        # same inbound reply anchor still owns an open stream and the callback
        # payload is exactly its token-bounded terminal text. Earlier/nonterminal
        # occurrences, another anchor, unopened streams, and every other send
        # continue through the ordinary QQ path.
        if (
            isinstance(metadata, dict)
            and metadata.get("_interim_send") is True
            and _is_c2c(self, str(chat_id))
        ):
            anchor = str(
                reply_to
                or metadata.get("reply_to_message_id")
                or ""
            ).strip()
            streams, anchors = _stream_maps(self)
            state_key = anchors.get((str(chat_id), anchor))
            state = streams.get(state_key) if state_key is not None else None
            if state is None and not anchor:
                # GatewayStreamConsumer._send_commentary currently omits its
                # initial_reply_to_id from metadata. Recover only when content
                # ownership identifies exactly one open stream in this C2C
                # chat. Multiple matching concurrent turns remain ambiguous
                # and deliberately fall through to the ordinary send.
                candidates = [
                    candidate
                    for candidate in streams.values()
                    if (
                        candidate.chat_id == str(chat_id)
                        and candidate.stream_msg_id
                        and not candidate.sealed
                        and _terminal_payload_is_owned(
                            _visible_stream_content(candidate),
                            str(content or ""),
                        )
                    )
                ]
                if len(candidates) == 1:
                    state = candidates[0]
            if (
                state is not None
                and state.stream_msg_id
                and not state.sealed
                and _terminal_payload_is_owned(
                    _visible_stream_content(state),
                    str(content or ""),
                )
            ):
                logger.debug(
                    "qqbot-connect-hotfix: suppressed already-streamed QQ "
                    "C2C interim carrier draft=%s",
                    state.draft_id,
                )
                return _send_result(
                    success=True,
                    message_id=state.stream_msg_id,
                    raw_response={"qq_stream_owned_interim": True},
                )

        # GatewayStreamConsumer marks its turn-final send with notify=True.
        # Only intercept that exact path: approvals, slash-command replies,
        # heartbeats, and steering acknowledgements must remain independent.
        if (
            isinstance(metadata, dict)
            and metadata.get("notify") is True
            and _is_c2c(self, str(chat_id))
        ):
            anchor = str(
                reply_to
                or metadata.get("reply_to_message_id")
                or ""
            ).strip()
            streams, anchors = _stream_maps(self)
            state_key = anchors.get((str(chat_id), anchor))
            state = streams.get(state_key) if state_key is not None else None
            if state is None:
                completed_owner = _completed_owner_for_final(
                    self,
                    chat_id=str(chat_id),
                    reply_to=anchor,
                    content=str(content or ""),
                )
                if completed_owner is not None:
                    return _send_result(
                        success=True,
                        message_id=completed_owner.message_id,
                        raw_response={"qq_completed_turn_owned": True},
                    )
                cancelled_owner = _cancelled_owner_for_anchor(
                    self,
                    chat_id=str(chat_id),
                    reply_to=anchor,
                )
                if cancelled_owner is not None:
                    normal_result = await original_send(
                        self,
                        chat_id,
                        content,
                        reply_to=reply_to,
                        metadata=metadata,
                    )
                    if getattr(normal_result, "success", False):
                        _promote_cancelled_owner(
                            self,
                            cancelled_owner,
                            final_content=str(content or ""),
                            message_id=getattr(
                                normal_result,
                                "message_id",
                                None,
                            ),
                        )
                    return normal_result
                pending_state = _final_only_pending_for_anchor(
                    self,
                    chat_id=str(chat_id),
                    reply_to=anchor,
                )
                if pending_state is not None:
                    normal_result = await original_send(
                        self,
                        chat_id,
                        content,
                        reply_to=reply_to,
                        metadata=metadata,
                    )
                    if getattr(normal_result, "success", False):
                        _remember_turn_tombstone(
                            self,
                            pending_state,
                            final_payload=str(content or ""),
                            final_content=str(content or ""),
                        )
                        _remove_final_only_pending(self, pending_state)
                    return normal_result
            if state is not None:
                if state.ordinary_owned_suffix:
                    # A prior successful final fallback is authoritative and
                    # immutable. Retried final callbacks may only close the
                    # still-open native prefix; they must not re-compose or
                    # re-send the suffix through either delivery channel.
                    closed = await _seal_stream(
                        self,
                        state,
                        state.last_content,
                    )
                    if closed.success:
                        return closed
                    return _send_result(
                        success=True,
                        message_id=state.stream_msg_id,
                        raw_response={"qq_stream_close_pending": True},
                    )
                if not state.stream_msg_id and not state.committed_prefix:
                    # Preserve the established final-only degradation when no
                    # native frame ever became visible. Retrying a first frame
                    # only at turn completion would change a known single-send
                    # fallback into a new native lifecycle.
                    normal_result = await original_send(
                        self,
                        chat_id,
                        content,
                        reply_to=reply_to,
                        metadata=metadata,
                    )
                    if getattr(normal_result, "success", False):
                        _remember_turn_tombstone(
                            self,
                            state,
                            final_payload=str(content or ""),
                            final_content=str(content or ""),
                        )
                        _remove_stream(self, state)
                    return normal_result

                # Hermes can finish with either the whole cumulative response
                # or a short final-only answer. Compose both forms against the
                # exact QQ-acknowledged prefix before applying one rollover
                # path, so no final suffix is capped or silently discarded.
                target = _compose_final_content(state, content)
                rollover_error = None
                try:
                    async with state.lock:
                        state, _data = await _send_cumulative_draft(
                            self,
                            state,
                            target,
                        )
                except Exception as exc:
                    rollover_error = exc
                    logger.warning(
                        "qqbot-connect-hotfix: final rollover stopped for "
                        "chat=%s draft=%s: %s",
                        chat_id,
                        state.draft_id,
                        exc,
                    )
                    # Rollover can seal heads and replace the map entry before
                    # a later operation fails. Ownership must be calculated
                    # from the latest acknowledged state, never the stale
                    # object captured before rollover.
                    latest_streams, _latest_anchors = _stream_maps(self)
                    latest_state = latest_streams.get(state_key)
                    if latest_state is not None:
                        state = latest_state

                unseen = _unseen_final_suffix(state, target)
                if unseen is None:
                    logger.error(
                        "qqbot-connect-hotfix: final ownership invariant lost "
                        "for chat=%s draft=%s; refusing duplicate fallback",
                        chat_id,
                        state.draft_id,
                    )
                    return _send_result(
                        success=False,
                        error="QQ stream final no longer extends visible content",
                    )

                if unseen:
                    # Only text that QQ has never acknowledged can enter the
                    # ordinary fallback. This remains correct whether failure
                    # happened before the head seal, after committed heads, or
                    # while opening a new tail.
                    normal_result = await original_send(
                        self,
                        chat_id,
                        unseen,
                        reply_to=reply_to,
                        metadata=metadata,
                    )
                    if getattr(normal_result, "success", False):
                        _remember_turn_tombstone(
                            self,
                            state,
                            final_payload=str(content or ""),
                            final_content=target,
                        )
                        if state.stream_msg_id:
                            state.ordinary_owned_suffix = unseen
                            recovery = await _seal_stream(
                                self,
                                state,
                                state.last_content,
                            )
                            if not recovery.success:
                                logger.warning(
                                    "qqbot-connect-hotfix: suffix fallback sent "
                                    "but visible stream close remains pending "
                                    "for chat=%s draft=%s: %s",
                                    chat_id,
                                    state.draft_id,
                                    recovery.error,
                                )
                        else:
                            _remove_stream(self, state)
                    return normal_result

                if not state.stream_msg_id and state.committed_prefix == target:
                    completed_id = state.last_completed_stream_id
                    _remember_turn_tombstone(
                        self,
                        state,
                        final_payload=str(content or ""),
                        final_content=target,
                    )
                    _remove_stream(self, state)
                    return _send_result(success=True, message_id=completed_id)

                if not state.stream_msg_id:
                    # No visible active stream and no unseen suffix is only
                    # possible for an empty final. Clear the placeholder.
                    _remember_turn_tombstone(
                        self,
                        state,
                        final_payload=str(content or ""),
                        final_content=target,
                    )
                    _remove_stream(self, state)
                    return _send_result(
                        success=True,
                        message_id=state.last_completed_stream_id,
                    )

                sealed = await _seal_stream(self, state, state.last_content)
                if sealed.success:
                    _remember_turn_tombstone(
                        self,
                        state,
                        final_payload=str(content or ""),
                        final_content=target,
                    )
                    return sealed

                # The whole target is already visible. Retry closing the same
                # body, but never emit an ordinary duplicate. If both bounded
                # close rounds fail, retain the state for abandon/retry and
                # report delivery success because every final character still
                # has exactly one visible owner.
                recovery = await _seal_stream(self, state, state.last_content)
                if recovery.success:
                    _remember_turn_tombstone(
                        self,
                        state,
                        final_payload=str(content or ""),
                        final_content=target,
                    )
                    return recovery
                logger.warning(
                    "qqbot-connect-hotfix: final is visible but stream close "
                    "remains pending for chat=%s draft=%s after rollover=%s: %s",
                    chat_id,
                    state.draft_id,
                    bool(rollover_error),
                    recovery.error,
                )
                return _send_result(
                    success=True,
                    message_id=state.stream_msg_id,
                    raw_response={"qq_stream_close_pending": True},
                )

        return await original_send(
            self,
            chat_id,
            content,
            reply_to=reply_to,
            metadata=metadata,
        )

    @functools.wraps(original_send_typing)
    async def send_typing(
        self,
        chat_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        chat_id = str(chat_id)
        if not _typing_budget_applies(self, chat_id):
            return await original_send_typing(self, chat_id, metadata=metadata)
        msg_id = str(getattr(self, "_last_msg_id", {}).get(chat_id) or "")
        if not msg_id:
            return await original_send_typing(self, chat_id, metadata=metadata)

        seen = getattr(self, "_qq_stream_typing_anchors", None)
        if seen is None:
            seen = {}
            self._qq_stream_typing_anchors = seen
        key = (chat_id, msg_id)
        if key in seen:
            return None
        seen[key] = None
        while len(seen) > _MAX_TYPING_ANCHORS:
            seen.pop(next(iter(seen)))
        return await original_send_typing(self, chat_id, metadata=metadata)

    setattr(send, _STREAM_PATCHED, True)
    setattr(send_draft, _STREAM_PATCHED, True)
    setattr(send_typing, _STREAM_PATCHED, True)
    QQAdapter.supports_draft_streaming = supports_draft_streaming
    QQAdapter.stream_is_message_for_chat = stream_is_message_for_chat
    QQAdapter.draft_stream_is_message = True
    QQAdapter.send_draft = send_draft
    QQAdapter.abandon_open_draft = abandon_open_draft
    QQAdapter.send = send
    QQAdapter.send_typing = send_typing
    gate_status = _patch_gateway_stream_gate(QQAdapter)
    overflow_status = _patch_gateway_overflow_limit(QQAdapter)
    logger.info("qqbot-connect-hotfix: %s", gate_status)
    logger.info("qqbot-connect-hotfix: %s", overflow_status)
    return "QQ C2C native streaming patched"
