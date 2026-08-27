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
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_STREAM_PATCHED = "_qqbot_native_c2c_streaming_patched"
_RUNNER_PATCHED = "_qqbot_native_c2c_streaming_runner_patched"
_OVERFLOW_PATCHED = "_qqbot_native_c2c_overflow_patched"
_MIN_HERMES_VERSION = (0, 20, 5)
_MAX_OPEN_STREAMS = 128
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
    sealed: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


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


def _remove_stream(adapter, state: _QQC2CStream) -> None:
    streams, anchors = _stream_maps(adapter)
    streams.pop(state.draft_id, None)
    anchor_key = (state.chat_id, state.reply_to)
    if anchors.get(anchor_key) == state.draft_id:
        anchors.pop(anchor_key, None)


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


def _native_lane_chats(adapter) -> set[str]:
    chats = getattr(adapter, "_qq_native_c2c_lane_chats", None)
    if chats is None:
        chats = set()
        adapter._qq_native_c2c_lane_chats = chats
    return chats


def _mark_native_lane(adapter, chat_id: str) -> None:
    if chat_id:
        _native_lane_chats(adapter).add(str(chat_id))


def _unmark_native_lane(adapter, chat_id: str) -> None:
    if chat_id:
        _native_lane_chats(adapter).discard(str(chat_id))


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


def _final_extends_visible_stream(state: _QQC2CStream, content: str) -> bool:
    """Whether final text is a cumulative extension of all visible chunks."""

    visible = str(state.committed_prefix or "") + str(state.last_content or "")
    return bool(visible) and str(content or "").startswith(visible)


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
    streams[state.draft_id] = state
    anchors[(state.chat_id, state.reply_to)] = state.draft_id


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


def _seal_content(adapter, state: _QQC2CStream, content: str) -> str:
    """Compose a legal final replace body without removing QQ's prefix.

    Hermes' draft contains commentary, tool progress, and often the final
    answer, while its turn-final ``send`` can contain only the short final
    answer. QQ rejects a replace request that removes an already-submitted
    prefix. Reuse the cumulative draft when it already contains the final; if
    it does not, append only the non-overlapping final suffix within the
    platform length limit.
    """

    previous = str(state.last_content or "")
    final = str(content or "")
    max_length = int(getattr(adapter, "MAX_MESSAGE_LENGTH", 4000))

    if not previous:
        return final[:max_length]
    if not final or final == previous:
        return previous
    if final.startswith(previous):
        return final[:max_length]
    if final in previous:
        return previous

    overlap = 0
    for size in range(min(len(previous), len(final)), 0, -1):
        if previous.endswith(final[:size]):
            overlap = size
            break

    suffix = final[overlap:]
    if suffix and not overlap and not previous.endswith(("\n", " ")):
        suffix = "\n" + suffix
    remaining = max(0, max_length - len(previous))
    return previous + suffix[:remaining]


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
        seal_content = _seal_content(adapter, state, content)
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
        state = streams.get(int(draft_id))
        if state is None:
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
                # is registered, the turn-final wrapper sends one normal final.
                return _send_result(success=True)
            state = _QQC2CStream(
                chat_id=chat_id,
                draft_id=int(draft_id),
                reply_to=reply_to,
                msg_seq=int(self._next_msg_seq(reply_to)),
            )
            streams[state.draft_id] = state
            anchors[(chat_id, reply_to)] = state.draft_id
            _mark_native_lane(self, chat_id)
        elif state.chat_id != chat_id or state.reply_to != reply_to:
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
        draft_id = anchors.get((str(chat_id), anchor))
        state = streams.get(draft_id) if draft_id is not None else None
        if state is None:
            return _send_result(success=True)
        active_content = _active_content(
            state,
            content or state.last_content,
            require_committed_prefix=False,
        )
        if not state.stream_msg_id and state.committed_prefix and not active_content:
            completed_id = state.last_completed_stream_id
            _remove_stream(self, state)
            return _send_result(success=True, message_id=completed_id)
        return await _seal_stream(
            self,
            state,
            active_content or state.last_content,
        )

    @functools.wraps(original_send)
    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        # GatewayStreamConsumer marks its turn-final send with notify=True.
        # Only intercept that exact path: approvals, slash-command replies,
        # heartbeats, and steering acknowledgements must remain independent.
        fallback_state = None
        recovery_state = None
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
            draft_id = anchors.get((str(chat_id), anchor))
            state = streams.get(draft_id) if draft_id is not None else None
            if state is not None:
                # A successful overflow rollover stores sealed heads in
                # committed_prefix. Keep the final seal scoped to the current
                # stream's suffix so every replace request preserves its own
                # accepted prefix.
                if _final_extends_visible_stream(state, content):
                    try:
                        async with state.lock:
                            state, _data = await _send_cumulative_draft(
                                self,
                                state,
                                content,
                            )
                    except Exception as exc:
                        logger.warning(
                            "qqbot-connect-hotfix: final overflow rollover "
                            "failed for chat=%s draft=%s: %s",
                            chat_id,
                            state.draft_id,
                            exc,
                        )
                        # Rollover may already have sealed one or more chunks
                        # and replaced the map entry before a new tail open
                        # failed. Continue with that latest active state, not
                        # the now-sealed state captured before the call.
                        latest_streams, _latest_anchors = _stream_maps(self)
                        latest_state = latest_streams.get(draft_id)
                        if latest_state is not None:
                            state = latest_state
                active_content = _active_content(
                    state,
                    content,
                    require_committed_prefix=False,
                )
                if (
                    not state.stream_msg_id
                    and state.committed_prefix
                    and not active_content
                ):
                    completed_id = state.last_completed_stream_id
                    _remove_stream(self, state)
                    return _send_result(success=True, message_id=completed_id)

                sealed = await _seal_stream(self, state, active_content)
                if sealed.success:
                    return sealed
                logger.warning(
                    "qqbot-connect-hotfix: falling back to normal C2C final "
                    "after stream seal failure for chat=%s: %s",
                    chat_id,
                    sealed.error,
                )
                fallback_state = state
                # The normal final is a safe visible fallback, but the opened
                # stream stays addressable so abandon/retry can still seal it.
                if state.stream_msg_id:
                    recovery_state = state

        normal_content = content
        if (
            fallback_state is not None
            and fallback_state.committed_prefix
            and str(content or "").startswith(fallback_state.committed_prefix)
        ):
            normal_content = str(content or "")[
                len(fallback_state.committed_prefix):
            ]

        normal_result = await original_send(
            self,
            chat_id,
            normal_content,
            reply_to=reply_to,
            metadata=metadata,
        )
        if (
            fallback_state is not None
            and not fallback_state.stream_msg_id
            and getattr(normal_result, "success", False)
        ):
            # No client-visible active stream remains: sealed overflow heads
            # plus the ordinary suffix now own the complete response.
            _remove_stream(self, fallback_state)
        if recovery_state is not None and getattr(normal_result, "success", False):
            # Once the complete ordinary final is visible, best-effort close
            # the older stream with its last acknowledged partial body. This
            # avoids duplicating the final answer in two bubbles. If QQ is
            # still unavailable, _seal_stream leaves the state retryable.
            recovery = await _seal_stream(
                self,
                recovery_state,
                recovery_state.last_content,
            )
            if not recovery.success:
                logger.warning(
                    "qqbot-connect-hotfix: QQ C2C fallback final sent but "
                    "stream close remains pending for chat=%s draft=%s: %s",
                    chat_id,
                    recovery_state.draft_id,
                    recovery.error,
                )
        return normal_result

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
