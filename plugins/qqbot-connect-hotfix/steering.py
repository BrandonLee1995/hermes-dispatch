"""QQ display segments for accepted Hermes steer/redirect, not new turns.

Hermes 0.20.5 keeps a native draft cumulative across tool boundaries. Its
busy-input handlers accept steering independently of the debounced ACK. Bind
those handlers to the active background task and pause its consumer at the
existing FIFO flush barrier before calling the agent. No ACK-text matching.
"""

import asyncio
import contextvars
import functools
import logging
import queue
from dataclasses import dataclass, field, replace

from . import streaming as stream

logger = logging.getLogger(__name__)
_ROUTE = contextvars.ContextVar("qq_reply_route", default=None)
_STEERING = contextvars.ContextVar("qq_accepted_steer", default=None)
_PATCHED = "_qq_display_steer_patched"


@dataclass
class _ReplyRoute:
    adapter: object
    chat_id: str
    initial_anchor: str
    anchor: str
    consumer: object = None
    running: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    barrier: object = None


class _FlushBarrier:
    """The upstream _FLUSH marker calls set() only after draining old output."""

    def __init__(self):
        self.ready = asyncio.Event()
        self.released = asyncio.Event()
        self.paused = False

    def set(self):
        if not self.released.is_set():
            self.paused = True
        self.ready.set()

    def release(self):
        self.paused = False
        self.released.set()


class _PausedQueue:
    def __init__(self, original, route):
        self.original, self.route = original, route

    def put(self, *args, **kwargs):
        return self.original.put(*args, **kwargs)

    def get_nowait(self):
        barrier = self.route.barrier
        if barrier is not None and barrier.paused:
            raise queue.Empty
        return self.original.get_nowait()


@dataclass
class _Steering:
    route: _ReplyRoute | None
    agent: object
    anchor: str
    accepted: bool = False
    rotated: bool = False

    async def rotate(self):
        if not self.accepted or self.rotated or self.route is None:
            return
        route = self.route
        consumer = route.consumer
        adapter = route.adapter
        old_anchor = route.anchor
        if old_anchor == self.anchor:
            self.rotated = True
            return

        async def move_segment():
            streams, _ = stream._stream_maps(adapter)
            old_draft = consumer._draft_id
            state = streams.get(stream._stream_key(route.chat_id, old_draft))
            prefix, completed_id = "", None
            if state is not None:
                if state.stream_msg_id and not state.retired:
                    result = await stream._seal_stream(adapter, state, state.last_content)
                    if not result.success:
                        # Bounded seal retries already ran. The old carrier is
                        # immutable locally; retain its visible prefix, leave
                        # deferred (unacknowledged) text in the consumer ledger.
                        logger.warning("qqbot-connect-hotfix: steer retiring unsealed draft=%s: %s", old_draft, result.error)
                prefix = stream._visible_stream_content(state)
                completed_id = state.stream_msg_id or state.last_completed_stream_id
                stream._remember_turn_tombstone(adapter, state, final_payload="", final_content=prefix, final_delivered=False)
                state.retired = True
                stream._remove_stream(adapter, state)

            # The old reply identity is closed, NOT the agent turn. Late raw
            # callbacks cannot reopen it; the live task's final is rebased below.
            stream._final_delivery_broker(adapter).remember_completed(
                (route.chat_id, old_anchor),
                stream._send_result(success=True, message_id=completed_id),
            )
            type(consumer)._draft_id_counter += 1
            consumer._draft_id = type(consumer)._draft_id_counter
            consumer._initial_reply_to_id = self.anchor
            consumer.metadata = {**(consumer.metadata or {}), "reply_to_message_id": self.anchor}
            consumer._qq_native_final_delta_segment = ""
            route.anchor = self.anchor
            stream._replace_active_stream(adapter, stream._QQC2CStream(
                chat_id=route.chat_id, draft_id=consumer._draft_id,
                reply_to=self.anchor, msg_seq=int(adapter._next_msg_seq(self.anchor)),
                committed_prefix=prefix, display_prefix=prefix,
                last_completed_stream_id=completed_id,
            ))
            stream._mark_native_lane(adapter, route.chat_id)
            self.rotated = True
            logger.info("qqbot-connect-hotfix: QQ steer display segment rotated old_draft=%s new_draft=%s", old_draft, consumer._draft_id)

        await stream._final_delivery_broker(adapter).coordinate((route.chat_id, old_anchor), move_segment)


def patch_qq_steering(QQAdapter):
    try:
        from gateway.platforms.base import BasePlatformAdapter
        from gateway.run import GatewayRunner
        from gateway.stream_consumer import GatewayStreamConsumer, _FLUSH
    except ImportError as exc:
        logger.warning("qqbot-connect-hotfix: steer display hooks unavailable: %s", exc)
        return "QQ steer display segments unavailable"

    original_background = BasePlatformAdapter._process_message_background
    if getattr(original_background, _PATCHED, False):
        return "QQ steer display segments already patched"
    original_init = GatewayStreamConsumer.__init__
    original_run = GatewayStreamConsumer.run
    original_edit = GatewayStreamConsumer._send_or_edit
    original_send = QQAdapter.send

    @functools.wraps(original_background)
    async def background(self, event, session_key):
        if not isinstance(self, QQAdapter) or not stream._is_c2c(self, str(event.source.chat_id)):
            return await original_background(self, event, session_key)
        routes = getattr(self, "_qq_reply_routes", None)
        if routes is None:
            routes = self._qq_reply_routes = {}
        anchor = str(event.message_id or "")
        route = _ReplyRoute(self, str(event.source.chat_id), anchor, anchor)
        routes[session_key] = route
        token = _ROUTE.set(route)
        try:
            return await original_background(self, event, session_key)
        finally:
            if route.barrier is not None:
                route.barrier.release()
            if routes.get(session_key) is route:
                routes.pop(session_key, None)
            _ROUTE.reset(token)

    @functools.wraps(original_init)
    def consumer_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        route = _ROUTE.get()
        if route is not None and self.adapter is route.adapter and str(self.chat_id) == route.chat_id:
            route.consumer = self
            self._qq_reply_route = route
            self._queue = _PausedQueue(self._queue, route)

    @functools.wraps(original_run)
    async def consumer_run(self):
        route = getattr(self, "_qq_reply_route", None)
        if route is None:
            return await original_run(self)
        route.running = True
        try:
            return await original_run(self)
        finally:
            route.running = False
            if route.barrier is not None:
                route.barrier.release()
                route.barrier.ready.set()

    @functools.wraps(original_edit)
    async def edit(self, *args, **kwargs):
        route = getattr(self, "_qq_reply_route", None)
        if route is not None and route.barrier is not None and route.barrier.paused:
            await route.barrier.released.wait()
            if not self._run_still_current():
                return False
        return await original_edit(self, *args, **kwargs)

    @functools.wraps(original_send)
    async def send(self, chat_id, content, reply_to=None, metadata=None):
        steering = _STEERING.get()
        if steering is not None and steering.route is not None and steering.route.adapter is self and steering.route.chat_id == str(chat_id):
            await steering.rotate()
        route = _ROUTE.get()
        if (route is not None and route.adapter is self and route.chat_id == str(chat_id)
                and isinstance(metadata, dict) and metadata.get("notify") is True
                and str(reply_to or metadata.get("reply_to_message_id") or "") == route.initial_anchor):
            # BasePlatformAdapter's fallback retains the original event. Only
            # that task's final is rebased; arbitrary old-anchor sends are not.
            reply_to = route.anchor
            metadata = {**metadata, "reply_to_message_id": route.anchor}
        return await original_send(self, chat_id, content, reply_to=reply_to, metadata=metadata)

    def observe_acceptance(original, *, native_steer=False):
        if getattr(original, _PATCHED, False):
            return original
        @functools.wraps(original)
        def accept(self, *args, **kwargs):
            steering = _STEERING.get()
            if (native_steer and steering is not None and steering.agent is self
                    and getattr(self, "api_mode", None) == "codex_app_server"):
                # Hermes steer() queues text for Hermes-owned tool batches,
                # which a Codex-owned turn never drains. Use its existing
                # native redirect implementation and observe the actual ACK.
                if not callable(getattr(getattr(self, "_codex_session", None), "request_steer", None)):
                    return False
                result = self.redirect(*args, **kwargs)
            else:
                result = original(self, *args, **kwargs)
            if result and steering is not None and steering.agent is self:
                steering.accepted = True
            return result
        setattr(accept, _PATCHED, True)
        return accept

    def wrap_busy(original, *, command=False):
        @functools.wraps(original)
        async def busy(self, event, session_key, *args, **kwargs):
            adapter = self._adapter_for_source(event.source)
            if not isinstance(adapter, QQAdapter) or not self._is_user_authorized(event.source):
                return await original(self, event, session_key, *args, **kwargs)

            async def invoke(active_route=None):
                state = self._peek_session_state(session_key)
                steering = _Steering(active_route, state.turn.agent if state else None, str(event.message_id or ""))
                # run_agent can discover plugins before AIAgent is defined.
                from run_agent import AIAgent
                AIAgent.steer = observe_acceptance(AIAgent.steer, native_steer=True)
                AIAgent.redirect = observe_acceptance(AIAgent.redirect)
                token = _STEERING.set(steering)
                try:
                    result = await original(self, event, session_key, *args, **kwargs)
                    await steering.rotate()  # also when ACK is disabled/debounced
                    if command and steering.accepted and active_route is not None:
                        if result:
                            try:
                                ack = await adapter._send_with_retry(
                                    chat_id=event.source.chat_id, content=result,
                                    reply_to=event.message_id,
                                    metadata=self._thread_metadata_for_source(event.source, event.message_id),
                                )
                                if not getattr(ack, "success", False):
                                    logger.warning("qqbot-connect-hotfix: steer ACK delivery failed")
                            except Exception:
                                logger.warning("qqbot-connect-hotfix: steer ACK delivery failed", exc_info=True)
                        return None  # already sent; never treat ACK as final
                    return result
                finally:
                    try:
                        # Accepted input survives cancellation of its ACK task.
                        await steering.rotate()
                    finally:
                        _STEERING.reset(token)

            route = getattr(adapter, "_qq_reply_routes", {}).get(session_key)
            consumer = route.consumer if route else None
            if (consumer is None or not route.running or not event.message_id
                    or not getattr(consumer, "_use_draft_streaming", False)
                    or getattr(self, "_draining", False)
                    or (not command and self._effective_busy_input_mode(event.source) not in {"steer", "interrupt"})):
                return await invoke()  # group/final-only control, no C2C transport
            async with route.lock:
                barrier = route.barrier = _FlushBarrier()
                consumer._queue.put((_FLUSH, barrier))
                try:
                    try:
                        await asyncio.wait_for(barrier.ready.wait(), timeout=5)
                    except asyncio.TimeoutError:
                        barrier.release()
                        # Never claim redirect when its output boundary could
                        # not be established. Preserve the input as next-turn
                        # work and keep the original consumer alive.
                        queued = replace(event, text=event.get_command_args().strip()) if command else event
                        self._queue_or_replace_pending_event(session_key, queued)
                        notice = "⏳ Output is still being delivered; your correction is queued for the next turn."
                        logger.warning("qqbot-connect-hotfix: steer flush barrier timed out; queued input without steering")
                        if command:
                            return notice
                        await adapter._send_with_retry(
                            chat_id=event.source.chat_id, content=notice,
                            reply_to=event.message_id,
                            metadata=self._thread_metadata_for_source(event.source, event.message_id),
                        )
                        return True
                    if not route.running or not consumer._run_still_current():
                        return await invoke()
                    return await invoke(route)
                finally:
                    barrier.release()
                    route.barrier = None
        return busy

    setattr(background, _PATCHED, True)
    BasePlatformAdapter._process_message_background = background
    GatewayStreamConsumer.__init__ = consumer_init
    GatewayStreamConsumer.run = consumer_run
    GatewayStreamConsumer._send_or_edit = edit
    QQAdapter.send = send
    GatewayRunner._handle_active_session_busy_message = wrap_busy(GatewayRunner._handle_active_session_busy_message)
    GatewayRunner._busy_steer_command = wrap_busy(GatewayRunner._busy_steer_command, command=True)
    return "QQ steer display segments patched"
