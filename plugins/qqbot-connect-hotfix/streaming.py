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
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_STREAM_PATCHED = "_qqbot_native_c2c_streaming_patched"
_MAX_OPEN_STREAMS = 128
_MAX_TYPING_ANCHORS = 1024


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


def _evict_streams(adapter) -> None:
    streams, anchors = _stream_maps(adapter)
    while len(streams) > _MAX_OPEN_STREAMS:
        _draft_id, state = next(iter(streams.items()))
        _remove_stream(adapter, state)
    # Defensive cleanup for anchors whose stream was removed independently.
    for anchor_key, draft_id in list(anchors.items()):
        if draft_id not in streams:
            anchors.pop(anchor_key, None)


def _reply_anchor(metadata: Optional[Dict[str, Any]]) -> str:
    if not isinstance(metadata, dict):
        return ""
    return str(metadata.get("reply_to_message_id") or "").strip()


def _is_c2c(adapter, chat_id: str, chat_type: Optional[str] = None) -> bool:
    normalized = str(chat_type or "").strip().lower()
    if normalized in {"dm", "c2c", "private"}:
        return True
    try:
        return str(adapter._guess_chat_type(chat_id)).lower() == "c2c"
    except Exception:
        return False


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
    state.last_content = str(content or "")
    return data


async def _seal_stream(adapter, state: _QQC2CStream, content: str):
    async with state.lock:
        if state.sealed:
            return _send_result(
                success=True,
                message_id=state.stream_msg_id,
            )
        if not state.stream_msg_id:
            _remove_stream(adapter, state)
            return _send_result(
                success=False,
                error="QQ stream cannot be sealed before its first frame",
            )
        try:
            data = await _post_stream_frame(
                adapter,
                state,
                content or state.last_content,
                input_state=10,
            )
        except Exception as exc:
            logger.warning(
                "qqbot-connect-hotfix: QQ C2C stream seal failed for chat=%s "
                "draft=%s: %s",
                state.chat_id,
                state.draft_id,
                exc,
            )
            return _send_result(success=False, error=str(exc))

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
        return bool(chat_id) and _is_c2c(self, str(chat_id), chat_type)

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
            state = _QQC2CStream(
                chat_id=chat_id,
                draft_id=int(draft_id),
                reply_to=reply_to,
                msg_seq=int(self._next_msg_seq(reply_to)),
            )
            streams[state.draft_id] = state
            anchors[(chat_id, reply_to)] = state.draft_id
            _evict_streams(self)
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
            if str(content or "") == state.last_content and state.stream_msg_id:
                return _send_result(success=True)
            try:
                data = await _post_stream_frame(
                    self,
                    state,
                    content,
                    input_state=1,
                )
            except Exception as exc:
                logger.warning(
                    "qqbot-connect-hotfix: QQ C2C stream frame failed for "
                    "chat=%s draft=%s index=%s: %s",
                    chat_id,
                    draft_id,
                    state.next_index,
                    exc,
                )
                # Disarm final interception. GatewayStreamConsumer will fall
                # back to the normal message path for this turn.
                _remove_stream(self, state)
                return _send_result(success=False, error=str(exc))

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
        return await _seal_stream(self, state, content or state.last_content)

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
                sealed = await _seal_stream(self, state, content)
                if sealed.success:
                    return sealed
                logger.warning(
                    "qqbot-connect-hotfix: falling back to normal C2C final "
                    "after stream seal failure for chat=%s: %s",
                    chat_id,
                    sealed.error,
                )
                _remove_stream(self, state)

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
    return "QQ C2C native streaming patched"
