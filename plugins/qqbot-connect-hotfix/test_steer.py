"""Real Gateway busy handlers + consumer + adapter lifecycle; QQ wire is fake."""

import asyncio
import os
import time
from types import SimpleNamespace
from unittest.mock import patch

from gateway.config import PlatformConfig, StreamingConfig
from gateway.platforms.base import MessageEvent, MessageType, SessionSource, build_session_key
from run_agent import AIAgent

from test_streaming import (
    BasePlatformAdapter, GatewayDummyAdapter, GatewayRunner,
    GatewayStreamConsumer, Platform, mod, hermes_version_tuple,
)


async def wait_until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.01)


class WireAdapter(GatewayDummyAdapter):
    def __init__(self):
        super().__init__()
        self.timeline = []
        self.cancel_seal_once = False
        self.fail_ack_once = False
        self.frame_entered = None
        self.frame_release = None

    async def _api_request(self, method, path, body):
        if self.frame_entered is not None and body["input_state"] == 1:
            self.frame_entered.set()
            await self.frame_release.wait()
        if body["input_state"] == 10 and self.cancel_seal_once:
            self.cancel_seal_once = False
            raise asyncio.CancelledError
        result = await super()._api_request(method, path, body)
        self.timeline.append(("frame", dict(body)))
        return result

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        if self.fail_ack_once:
            self.fail_ack_once = False
            raise TimeoutError("ACK response lost")
        result = await super().send(chat_id, content, reply_to=reply_to, metadata=metadata)
        if result.success:
            self.timeline.append(("message", content))
        return result


async def redirect_case(name="redirect", *, after="AFTER", final="DONE", final_delta=True,
                        expected=("BEFORE", "AFTERDONE"), accepted=True, command=False,
                        silent=False, debounce=False, seal_failures=0, repeat=False,
                        fallback=False, finish_on_accept=False, before="BEFORE",
                        unauthorized=False, delayed_ack=False, cancelled_seal=False,
                        failed_ack=False, deferred=False, overflow=False,
                        barrier_timeout=False, stopped=False, busy_mode="interrupt"):
    adapter = WireAdapter()
    BasePlatformAdapter.__init__(adapter, PlatformConfig(typing_indicator=False), Platform.QQBOT)
    source = SessionSource(platform=Platform.QQBOT, chat_id="test-user", chat_type="dm", user_id="test-user")
    key = build_session_key(source)
    event = MessageEvent(text="start", message_type=MessageType.TEXT, source=source, message_id="original")
    correction = MessageEvent(text="change direction", message_type=MessageType.TEXT, source=source, message_id="correction")
    runner = object.__new__(GatewayRunner)
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._pending_messages = {}
    runner._busy_ack_ts = {}
    runner._draining = False
    runner._busy_input_mode = busy_mode
    runner.adapters = {Platform.QQBOT: adapter}
    runner.config = SimpleNamespace(group_sessions_per_user=True, thread_sessions_per_user=False)
    runner._is_user_authorized = lambda source: not unauthorized
    agent = object.__new__(AIAgent)
    agent.api_mode = "codex_app_server"
    agent._interrupt_requested = False
    agent._supports_active_turn_redirect = True
    finish = asyncio.Event()
    consumers = []
    accepted_calls = []
    current_run = [True]

    def native_steer(text):
        # Backend emits immediately while Gateway is still delivering the ACK.
        accepted_calls.append(text)
        if accepted:
            consumers[0].on_delta(after)
            if finish_on_accept:
                finish.set()
        return accepted

    agent._codex_session = SimpleNamespace(request_steer=native_steer)
    runner._session_state(key).turn.agent = agent

    async def backend(event):
        if event.message_id != "original":
            return None  # A rejected steer retains upstream's next-turn queue.
        cfg, _ = runner._build_stream_consumer_config(source, StreamingConfig(enabled=True, transport="auto"), adapter, on_missing_cursor="raise")
        cfg.edit_interval, cfg.buffer_threshold, cfg.cursor = 0.01, 1, ""
        consumer = GatewayStreamConsumer(
            adapter, source.chat_id,
            config=cfg,
            initial_reply_to_id=event.message_id,
            run_still_current=lambda: current_run[0],
        )
        consumers.append(consumer)
        task = asyncio.create_task(consumer.run())
        consumer.on_delta(before)
        await finish.wait()
        consumer.on_segment_break()
        if final_delta:
            consumer.on_delta(final)
        consumer.finish(final)
        await task
        return final if fallback else None

    adapter.set_message_handler(backend)
    task = asyncio.create_task(adapter._process_message_background(event, key))
    try:
        await wait_until(lambda: len(adapter.successful_api_calls) > 0 if before else consumers and consumers[0]._draft_id is not None)
        old_draft = consumers[0]._draft_id
        adapter.fail_seal_attempts = seal_failures
        adapter.cancel_seal_once = cancelled_seal
        adapter.fail_ack_once = failed_ack
        if deferred:
            adapter.fail_stream_attempts = 1
            consumers[0].on_delta("UNSENT")
            await wait_until(lambda: adapter.fail_stream_attempts == 0)
        if barrier_timeout:
            adapter.frame_entered = asyncio.Event()
            adapter.frame_release = asyncio.Event()
            consumers[0].on_delta("PENDING")
            await adapter.frame_entered.wait()
        if debounce:
            runner._session_state(key).turn.busy_ack_ts = time.time()
        with patch("gateway.run._load_gateway_config", return_value={}), patch("agent.onboarding.is_seen", return_value=True), patch.dict(os.environ, {"HERMES_GATEWAY_BUSY_ACK_ENABLED": "false" if silent else "true"}):
            if command:
                correction.text = "/steer change direction"
                result = await runner._busy_steer_command(correction, key, source)
                if result:
                    await adapter._send_with_retry(source.chat_id, result, reply_to=correction.message_id, metadata={"notify": True})
                assert len(accepted_calls) == 1, "/steer never reached Codex turn/steer"
            else:
                if delayed_ack:
                    adapter.normal_send_entered = asyncio.Event()
                    adapter.normal_send_release = asyncio.Event()
                    busy = asyncio.create_task(runner._handle_active_session_busy_message(correction, key))
                    await adapter.normal_send_entered.wait()
                    await asyncio.sleep(0.08)
                    assert not any(after in call[2]["content_raw"] for call in adapter.successful_api_calls)
                    if stopped:
                        current_run[0] = False
                    adapter.normal_send_release.set()
                    await busy
                else:
                    try:
                        await runner._handle_active_session_busy_message(correction, key)
                    except asyncio.CancelledError:
                        assert cancelled_seal
                if barrier_timeout:
                    adapter.frame_release.set()
            if repeat:
                await wait_until(lambda: any(after in call[2]["content_raw"] for call in adapter.successful_api_calls))
                correction.message_id = "correction-2"
                await runner._handle_active_session_busy_message(correction, key)
        if after and accepted and not stopped:
            await wait_until(lambda: any(after[:4000] in call[2]["content_raw"] for call in adapter.successful_api_calls))
        finish.set()
        await task
        seals = [call[2] for call in adapter.successful_api_calls if call[2]["input_state"] == 10]
        assert [frame["content_raw"] for frame in seals] == list(expected), seals
        opens = [call[2] for call in adapter.successful_api_calls if call[2]["index"] == 0]
        anchors = (["original"] if before else []) + (["correction"] if accepted and not stopped else []) + (["correction-2"] if repeat else [])
        if overflow:
            anchors.append("correction")
        assert [frame["msg_id"] for frame in opens] == anchors, opens
        assert len(adapter.normal_sends) == (0 if silent or debounce or unauthorized or cancelled_seal or failed_ack else 1), adapter.normal_sends
        if adapter.normal_sends:
            word = ("Output is still being delivered" if barrier_timeout else
                    "Steer rejected" if command and not accepted else
                    "Steer queued" if command else
                    "Steered" if busy_mode == "steer" else
                    "Redirected" if accepted else "Interrupting")
            assert word in adapter.normal_sends[0][1], adapter.normal_sends
        if accepted and not silent and not debounce and before and not cancelled_seal and not failed_ack and not stopped:
            ack_index = next(i for i, item in enumerate(adapter.timeline) if item[0] == "message")
            new_open = next(i for i, item in enumerate(adapter.timeline) if item[0] == "frame" and item[1]["msg_id"] == "correction")
            if seal_failures < 3:
                old_close = next(i for i, item in enumerate(adapter.timeline) if item[0] == "frame" and item[1]["input_state"] == 10)
                assert old_close < ack_index, adapter.timeline
            assert ack_index < new_open, adapter.timeline
        if accepted:
            count = len(adapter.api_calls), len(adapter.normal_sends)
            await adapter.send_draft(source.chat_id, old_draft, "LATE OLD", metadata={"reply_to_message_id": "original"})
            await adapter.send(source.chat_id, "LATE FINAL", reply_to="original", metadata={"notify": True})
            assert (len(adapter.api_calls), len(adapter.normal_sends)) == count
        assert len(accepted_calls) == (0 if unauthorized or barrier_timeout else 2 if repeat else 1)
        print(f"qq_steer_{name}=ok")
    finally:
        finish.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def group_command():
    adapter = WireAdapter()
    source = SessionSource(platform=Platform.QQBOT, chat_id="group-test", chat_type="group", user_id="test-user")
    runner = object.__new__(GatewayRunner)
    runner.adapters = {Platform.QQBOT: adapter}
    runner._is_user_authorized = lambda source: True
    agent = object.__new__(AIAgent)
    agent.api_mode = "codex_app_server"
    agent._interrupt_requested = False
    calls = []
    agent._codex_session = SimpleNamespace(request_steer=lambda text: calls.append(text) or True)
    key = build_session_key(source)
    runner._session_state(key).turn.agent = agent
    event = MessageEvent(text="/steer group correction", message_type=MessageType.TEXT, source=source, message_id="group-correction")
    result = await runner._busy_steer_command(event, key, source)
    assert calls == ["group correction"], "group /steer never reached Codex"
    assert result and "Steer queued" in result
    assert not adapter.api_calls and not adapter.normal_sends
    print("qq_steer_group_native_control_without_c2c_transport=ok")


async def main():
    status = mod._patch_qq_c2c_streaming(WireAdapter)
    if hermes_version_tuple() < (0, 20, 5):
        assert status.startswith("QQ C2C native streaming disabled")
        assert not getattr(BasePlatformAdapter._process_message_background, "_qq_display_steer_patched", False)
        print("qq_steer_legacy_fail_closed=ok")
        return
    await redirect_case()
    await redirect_case("same_text_independent_final", after="", final="BEFORE", final_delta=False, expected=("BEFORE", "BEFORE"))
    await redirect_case("explicit_command", command=True)
    await redirect_case("explicit_command_rejected", command=True, accepted=False, after="", expected=("BEFOREDONE",))
    await redirect_case("busy_steer_mode", busy_mode="steer")
    await group_command()
    await redirect_case("silent_ack", silent=True)
    await redirect_case("debounced_ack", debounce=True)
    await redirect_case("two_steers", repeat=True, expected=("BEFORE", "AFTER", "AFTERDONE"))
    await redirect_case("native_final_race", finish_on_accept=True)
    await redirect_case("outer_fallback_rebased", fallback=True)
    await redirect_case("seal_retry", seal_failures=1)
    await redirect_case("seal_exhausted_retire", seal_failures=3, expected=("AFTERDONE",))
    await redirect_case("rejected_steer", accepted=False, after="", expected=("BEFOREDONE",))
    await redirect_case("unauthorized", unauthorized=True, accepted=False, after="", expected=("BEFOREDONE",))
    await redirect_case("before_first_delta", before="", expected=("AFTERDONE",))
    await redirect_case("slow_ack_final_race", delayed_ack=True, finish_on_accept=True)
    await redirect_case("cancelled_seal", cancelled_seal=True)
    await redirect_case("busy_ack_timeout_no_reinject", failed_ack=True)
    await redirect_case("command_ack_timeout_no_reinject", command=True, failed_ack=True)
    await redirect_case("deferred_old_tail_preserved", deferred=True, expected=("BEFORE", "UNSENTAFTERDONE"))
    await redirect_case("post_steer_overflow", after="N" * 4001, expected=("BEFORE", "N" * 4000, "NDONE"), overflow=True)
    await redirect_case("barrier_timeout_queues", barrier_timeout=True, accepted=False, after="", expected=("BEFOREPENDINGDONE",))
    await redirect_case("stop_during_ack", delayed_ack=True, stopped=True, expected=("BEFORE",))
    await asyncio.gather(redirect_case("parallel_a", delayed_ack=True), redirect_case("parallel_b"))
    other_agent = object.__new__(AIAgent)
    other_agent.api_mode = "codex_app_server"
    unrelated_calls = []
    other_agent._codex_session = SimpleNamespace(request_steer=lambda text: unrelated_calls.append(text))
    assert other_agent.steer("outside QQ handler")
    assert not unrelated_calls
    print("qq_steer_other_context_unchanged=ok")


if __name__ == "__main__":
    asyncio.run(main())
