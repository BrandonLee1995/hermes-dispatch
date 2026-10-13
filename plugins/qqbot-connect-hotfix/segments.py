"""Rotate C2C display bubbles only at native Hermes Working reminders."""
import functools
import logging
import sys

from . import streaming as stream
from .steering import _ROUTE, _STEERING

logger = logging.getLogger(__name__)
_PATCHED = "_qq_display_segments_patched"
_BOUNDARY = "_qq_working_boundary"


async def _rotate(adapter, state):
    """Keep the cumulative delivery ledger while replacing only its carrier."""
    async with state.lock:
        if not state.stream_msg_id or state.sealed:
            return
        visible = stream._visible_stream_content(state)
        completed_id = state.stream_msg_id
        if not state.retired:
            _data, error = await stream._post_seal_with_retries(adapter, state, state.last_content)
            if error is not None:
                # The acknowledged body is immutable locally even if QQ cannot
                # acknowledge its close. Never append later output behind a status.
                logger.warning("QQ C2C segment close failed; retaining visible prefix and opening a new carrier")
        stream._cancel_stream_expiry(state)
        state.committed_prefix = visible
        state.display_prefix = visible
        state.last_completed_stream_id = completed_id
        state.msg_seq = int(adapter._next_msg_seq(state.reply_to))
        state.stream_msg_id = None
        state.next_index = 0
        state.last_content = ""
        state.opened_monotonic = None
        state.ambiguous_frame = None
        state.retired = state.passive_reply_exhausted
        stream._clear_stream_frame_cooldown(state, keep_deferred=True)
        logger.info("QQ C2C display segment closed draft=%s cumulative_chars=%s; next output uses a new bubble",
                    state.draft_id, len(visible))


def patch_qq_display_segments(QQAdapter):
    import gateway.run as gateway_run
    original = QQAdapter.send
    if getattr(original, _PATCHED, False):
        return "QQ display segments already patched"
    original_metadata = gateway_run._interim_metadata

    @functools.wraps(original_metadata)
    def interim_metadata(metadata=None):
        result = original_metadata(metadata)
        route = _ROUTE.get()
        # Hermes shares this helper with approvals/warnings; only its native
        # heartbeat callback defines a display boundary, independent of wording.
        caller = sys._getframe(1).f_code
        if (caller.co_name == "_notify_long_running"
                and caller.co_filename == gateway_run.__file__
                and route is not None and isinstance(route.adapter, QQAdapter)
                and stream._is_c2c(route.adapter, route.chat_id)):
            result[_BOUNDARY] = True
        return result

    @functools.wraps(original)
    async def send(self, chat_id, content, reply_to=None, metadata=None):
        async def ordinary():
            return await original(self, chat_id, content, reply_to=reply_to, metadata=metadata)

        if (not isinstance(metadata, dict) or not metadata.get("_interim_send")
                or not metadata.get(_BOUNDARY) or metadata.get("notify")
                or not stream._is_c2c(self, str(chat_id)) or _STEERING.get() is not None):
            return await ordinary()
        route = _ROUTE.get()
        anchor = str(reply_to or metadata.get("reply_to_message_id") or "")
        if route is not None and route.adapter is self and route.chat_id == str(chat_id) and anchor in ("", route.initial_anchor):
            anchor = route.anchor
        if not anchor:
            return await ordinary()

        async def boundary():
            states, anchors = stream._stream_maps(self)
            key = anchors.get((str(chat_id), anchor))
            state = states.get(key) if key is not None else None
            if state is None or state.close_pending_final_content is not None or state.ordinary_owned_suffix:
                return await ordinary()
            await _rotate(self, state)
            result = await ordinary()
            logger.info("QQ C2C Working boundary draft=%s delivered=%s", state.draft_id, bool(result.success))
            return result

        return await stream._final_delivery_broker(self).coordinate((str(chat_id), anchor), boundary)

    setattr(send, _PATCHED, True)
    QQAdapter.send = send
    gateway_run._interim_metadata = interim_metadata
    return "QQ C2C Working-delimited display segments patched"
