import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import anyio

from gateway.platforms.base import BasePlatformAdapter
from gateway.config import Platform, StreamingConfig
import gateway.run as gateway_run
from gateway.run import GatewayRunner
from gateway.stream_consumer import GatewayStreamConsumer, StreamConsumerConfig


def load_plugin_module():
    path = Path(__file__).with_name("__init__.py")
    spec = importlib.util.spec_from_file_location(
        "qqbot_connect_streaming_test",
        path,
        submodule_search_locations=[str(path.parent)],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = spec.name
    mod.__path__ = [str(path.parent)]
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


mod = load_plugin_module()
streaming_mod = sys.modules[mod.__name__ + ".streaming"]


def hermes_version_tuple():
    return streaming_mod._hermes_version_tuple()


class DummyAdapter:
    MAX_MESSAGE_LENGTH = 4000

    def __init__(self):
        self._app_id = "test-app"
        self._markdown_support = True
        self._last_msg_id = {}
        self.api_calls = []
        self.normal_sends = []
        self.typing_calls = []
        self.stream_counter = 0
        self.fail_next_stream = False
        self.fail_seal_attempts = 0
        self.fail_tail_open_attempts = 0

    def _guess_chat_type(self, chat_id):
        chat_id = str(chat_id)
        if chat_id.startswith("group-"):
            return "group"
        if chat_id.startswith("guild-dm-"):
            return "dm"
        return "c2c"

    def _next_msg_seq(self, key):
        return 73

    async def _api_request(self, method, path, body):
        self.api_calls.append((method, path, dict(body)))
        if self.fail_next_stream:
            self.fail_next_stream = False
            raise RuntimeError("stream unavailable")
        if (
            body["index"] == 0
            and len(body["content_raw"]) == 100
            and self.fail_tail_open_attempts
        ):
            self.fail_tail_open_attempts -= 1
            raise RuntimeError("tail open unavailable")
        if body["input_state"] == 10 and self.fail_seal_attempts:
            self.fail_seal_attempts -= 1
            raise RuntimeError("seal unavailable")
        self.stream_counter += 1
        if body["index"] == 0:
            return {"id": f"stream-{self.stream_counter}"}
        return {"id": body["stream_msg_id"]}

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        self.normal_sends.append((chat_id, content, reply_to, metadata))
        return SimpleNamespace(
            success=True,
            message_id="normal-message",
            error=None,
        )

    async def send_typing(self, chat_id, metadata=None):
        self.typing_calls.append((chat_id, metadata))


class GatewayDummyAdapter(DummyAdapter, BasePlatformAdapter):
    """Real consumer-compatible adapter while retaining the wire fake."""


GatewayDummyAdapter.__abstractmethods__ = frozenset()
GatewayDummyAdapter.SUPPORTS_MESSAGE_EDITING = False


async def main():
    status = mod._patch_qq_c2c_streaming(DummyAdapter)
    if hermes_version_tuple() < (0, 20, 5):
        assert status.startswith(
            "QQ C2C native streaming disabled: requires Hermes >=0.20.5"
        )
        assert not hasattr(DummyAdapter, "send_draft")
        assert not getattr(
            GatewayRunner._build_stream_consumer_config,
            streaming_mod._RUNNER_PATCHED,
            False,
        )
        legacy = DummyAdapter()
        legacy._last_msg_id["user-disabled"] = "disabled-1"
        await legacy.send_typing("user-disabled")
        await legacy.send_typing("user-disabled")
        assert len(legacy.typing_calls) == 2
        print("qq_c2c_hermes_0_20_0_fail_closed=ok")
        print("qq_c2c_disabled_typing_unchanged=ok")
        return

    assert status == "QQ C2C native streaming patched"
    assert mod._patch_qq_c2c_streaming(DummyAdapter).endswith("already patched")

    # Only an exact stable release version may enable the patch. Pre-release
    # suffixes fail closed even when their numeric core is 0.20.5.
    import hermes_cli

    original_version = hermes_cli.__version__
    try:
        for candidate in ("0.20.5rc1", "0.20.5.dev0", "0.20.5+local"):
            hermes_cli.__version__ = candidate
            assert streaming_mod._hermes_version_tuple() == ()
            assert not streaming_mod._hermes_streaming_supported()
        hermes_cli.__version__ = "0.20.5"
        assert streaming_mod._hermes_version_tuple() == (0, 20, 5)
        assert streaming_mod._hermes_streaming_supported()
    finally:
        hermes_cli.__version__ = original_version

    adapter = DummyAdapter()
    assert not adapter.supports_draft_streaming(chat_type="dm", chat_id="user-1")
    assert not adapter.supports_draft_streaming(
        chat_type="group", chat_id="group-1"
    )
    assert adapter.stream_is_message_for_chat("user-1")
    assert not adapter.stream_is_message_for_chat("group-1")
    assert not adapter.stream_is_message_for_chat("guild-dm-1")
    rejected_group = await adapter.send_draft(
        "group-1", 999, "不应发送", {"reply_to_message_id": "group-msg"}
    )
    assert not rejected_group.success
    rejected_guild_dm = await adapter.send_draft(
        "guild-dm-1",
        998,
        "不应发送",
        {"reply_to_message_id": "guild-dm-msg"},
    )
    assert not rejected_guild_dm.success
    assert not adapter.api_calls

    metadata = {"reply_to_message_id": "inbound-1"}
    first = await adapter.send_draft("user-1", 1001, "正在读取", metadata)
    second = await adapter.send_draft("user-1", 1001, "正在读取知识库", metadata)
    assert adapter.supports_draft_streaming(chat_type="dm", chat_id="user-1")
    assert first.success and second.success
    assert len(adapter.api_calls) == 2
    first_body = adapter.api_calls[0][2]
    second_body = adapter.api_calls[1][2]
    assert first_body == {
        "input_mode": "replace",
        "input_state": 1,
        "index": 0,
        "content_type": "markdown",
        "content_raw": "正在读取",
        "msg_id": "inbound-1",
        "msg_seq": 73,
    }
    assert second_body["index"] == 1
    assert second_body["stream_msg_id"].startswith("stream-")
    assert second_body["content_raw"] == "正在读取知识库"

    final = await adapter.send(
        "user-1",
        "最终答案",
        reply_to="inbound-1",
        metadata={"notify": True, "reply_to_message_id": "inbound-1"},
    )
    assert final.success
    assert final.message_id.startswith("stream-")
    assert adapter.api_calls[-1][2]["input_state"] == 10
    assert (
        adapter.api_calls[-1][2]["content_raw"]
        == "正在读取知识库\n最终答案"
    )
    assert not adapter.normal_sends

    # Hermes can stream user-visible commentary before it produces the short
    # turn-final answer. QQ replace mode forbids removing an already-delivered
    # prefix, so sealing must retain the cumulative draft instead of replacing
    # it with the shorter final-only string.
    prefixed = DummyAdapter()
    prefixed_metadata = {"reply_to_message_id": "inbound-prefix"}
    await prefixed.send_draft(
        "user-prefix",
        1003,
        "开始只读检查。",
        prefixed_metadata,
    )
    await prefixed.send_draft(
        "user-prefix",
        1003,
        "开始只读检查。\n最终答案",
        prefixed_metadata,
    )
    prefixed_final = await prefixed.send(
        "user-prefix",
        "最终答案",
        reply_to="inbound-prefix",
        metadata={
            "notify": True,
            "reply_to_message_id": "inbound-prefix",
        },
    )
    assert prefixed_final.success
    assert prefixed.api_calls[-1][2]["input_state"] == 10
    assert (
        prefixed.api_calls[-1][2]["content_raw"]
        == "开始只读检查。\n最终答案"
    )
    assert not prefixed.normal_sends

    # A non-final message must never seal or hijack the open stream.
    await adapter.send_draft(
        "user-2",
        1002,
        "处理中",
        {"reply_to_message_id": "inbound-2"},
    )
    ordinary = await adapter.send(
        "user-2",
        "审批提示",
        reply_to="inbound-2",
        metadata={"non_conversational": True},
    )
    assert ordinary.success
    assert adapter.normal_sends[-1][1] == "审批提示"
    await adapter.send(
        "user-2",
        "完成",
        reply_to="inbound-2",
        metadata={"notify": True, "reply_to_message_id": "inbound-2"},
    )

    # Two simultaneous DMs keep independent stream ids and indices.
    await adapter.send_draft(
        "user-a", 2001, "A1", {"reply_to_message_id": "msg-a"}
    )
    await adapter.send_draft(
        "user-b", 2002, "B1", {"reply_to_message_id": "msg-b"}
    )
    await adapter.send_draft(
        "user-a", 2001, "A2", {"reply_to_message_id": "msg-a"}
    )
    bodies = [call[2] for call in adapter.api_calls[-3:]]
    assert [body["index"] for body in bodies] == [0, 0, 1]
    assert [body["msg_id"] for body in bodies] == ["msg-a", "msg-b", "msg-a"]
    assert "stream_msg_id" not in bodies[0]
    assert "stream_msg_id" not in bodies[1]
    assert bodies[2]["stream_msg_id"].startswith("stream-")

    # Cancelling a turn seals the visible stream instead of leaving it live.
    abandoned = await adapter.abandon_open_draft(
        "user-a",
        "任务已停止",
        {"reply_to_message_id": "msg-a"},
    )
    assert abandoned.success
    assert adapter.api_calls[-1][2]["input_state"] == 10

    # A failed first frame stays on the native lane so Hermes cannot emit an
    # uneditable partial. The turn-final wrapper then falls back to exactly
    # one original normal message when no stream ever opened.
    fallback = DummyAdapter()
    fallback.fail_next_stream = True
    failed = await fallback.send_draft(
        "user-f", 3001, "处理中", {"reply_to_message_id": "msg-f"}
    )
    assert failed.success
    normal = await fallback.send(
        "user-f",
        "最终回退",
        reply_to="msg-f",
        metadata={"notify": True, "reply_to_message_id": "msg-f"},
    )
    assert normal.success
    assert fallback.normal_sends[-1][1] == "最终回退"

    # A transient seal error retries the same acknowledged index and closes
    # the stream without emitting an ordinary duplicate final.
    seal_retry = DummyAdapter()
    await seal_retry.send_draft(
        "user-seal-retry",
        3101,
        "处理中",
        {"reply_to_message_id": "msg-seal-retry"},
    )
    seal_retry.fail_seal_attempts = 1
    retried = await seal_retry.send(
        "user-seal-retry",
        "最终答案",
        reply_to="msg-seal-retry",
        metadata={"notify": True, "reply_to_message_id": "msg-seal-retry"},
    )
    assert retried.success
    assert not seal_retry.normal_sends
    retry_streams, _retry_anchors = streaming_mod._stream_maps(seal_retry)
    assert not retry_streams

    # After a failed final seal, the ordinary visible final lands first and a
    # second bounded attempt closes the old partial without duplicating final
    # content in that stream.
    seal_degrade = DummyAdapter()
    await seal_degrade.send_draft(
        "user-seal-degrade",
        3102,
        "处理中",
        {"reply_to_message_id": "msg-seal-degrade"},
    )
    seal_degrade.fail_seal_attempts = len(streaming_mod._SEAL_RETRY_DELAYS)
    degraded = await seal_degrade.send(
        "user-seal-degrade",
        "最终回退",
        reply_to="msg-seal-degrade",
        metadata={
            "notify": True,
            "reply_to_message_id": "msg-seal-degrade",
        },
    )
    assert degraded.success
    assert seal_degrade.normal_sends[-1][1] == "最终回退"
    degrade_streams, _degrade_anchors = streaming_mod._stream_maps(seal_degrade)
    assert not degrade_streams
    assert seal_degrade.api_calls[-1][2]["input_state"] == 10
    assert seal_degrade.api_calls[-1][2]["content_raw"] == "处理中"

    # If both the final seal and post-fallback close remain unavailable, the
    # state is still addressable for an explicit later close retry.
    seal_recover = DummyAdapter()
    await seal_recover.send_draft(
        "user-seal-recover",
        3103,
        "处理中",
        {"reply_to_message_id": "msg-seal-recover"},
    )
    seal_recover.fail_seal_attempts = len(streaming_mod._SEAL_RETRY_DELAYS) * 2
    recovered_fallback = await seal_recover.send(
        "user-seal-recover",
        "最终回退",
        reply_to="msg-seal-recover",
        metadata={
            "notify": True,
            "reply_to_message_id": "msg-seal-recover",
        },
    )
    assert recovered_fallback.success
    assert seal_recover.normal_sends[-1][1] == "最终回退"
    recover_streams, _recover_anchors = streaming_mod._stream_maps(seal_recover)
    assert 3103 in recover_streams
    closed_after_failure = await seal_recover.abandon_open_draft(
        "user-seal-recover",
        "最终回退",
        {"reply_to_message_id": "msg-seal-recover"},
    )
    assert closed_after_failure.success
    assert 3103 not in recover_streams

    # Capacity pressure never discards an opened stream. The extra turn stays
    # final-only, while both existing streams remain sealable.
    capacity = DummyAdapter()
    previous_capacity = streaming_mod._MAX_OPEN_STREAMS
    streaming_mod._MAX_OPEN_STREAMS = 2
    try:
        await capacity.send_draft(
            "user-cap-a", 3201, "A", {"reply_to_message_id": "msg-cap-a"}
        )
        await capacity.send_draft(
            "user-cap-b", 3202, "B", {"reply_to_message_id": "msg-cap-b"}
        )
        before_extra = len(capacity.api_calls)
        extra = await capacity.send_draft(
            "user-cap-c", 3203, "C", {"reply_to_message_id": "msg-cap-c"}
        )
        assert extra.success
        assert len(capacity.api_calls) == before_extra
        capacity_streams, _capacity_anchors = streaming_mod._stream_maps(capacity)
        assert set(capacity_streams) == {3201, 3202}
        capacity_final = await capacity.send(
            "user-cap-c",
            "C final",
            reply_to="msg-cap-c",
            metadata={"notify": True, "reply_to_message_id": "msg-cap-c"},
        )
        assert capacity_final.success
        assert capacity.normal_sends[-1][1] == "C final"
        assert set(capacity_streams) == {3201, 3202}
    finally:
        streaming_mod._MAX_OPEN_STREAMS = previous_capacity

    # With streaming disabled/no native lane, preserve upstream periodic
    # typing behavior exactly.
    disabled_typing = DummyAdapter()
    disabled_typing._last_msg_id["user-disabled"] = "typing-disabled"
    await disabled_typing.send_typing("user-disabled")
    await disabled_typing.send_typing("user-disabled")
    await disabled_typing.send_typing("user-disabled")
    assert len(disabled_typing.typing_calls) == 3

    # An active native lane is bounded to one passive input_notify per inbound
    # msg_id so the final retains its passive-reply budget.
    typing = DummyAdapter()
    typing._last_msg_id["user-t"] = "typing-1"
    await typing.send_draft(
        "user-t", 3301, "处理中", {"reply_to_message_id": "typing-1"}
    )
    await typing.send_typing("user-t")
    await typing.send_typing("user-t")
    await typing.send_typing("user-t")
    assert len(typing.typing_calls) == 1
    typing._last_msg_id["user-t"] = "typing-2"
    await typing.send_typing("user-t")
    assert len(typing.typing_calls) == 2

    # Hermes' in-process runner normally rejects non-editable adapters before
    # the consumer can probe native draft support. The hotfix bypasses that
    # legacy gate only for QQ C2C; group chats retain the rejection.
    gate_adapter = GatewayDummyAdapter()
    runner = object.__new__(GatewayRunner)
    scfg = StreamingConfig(enabled=True, transport="auto")
    original_config_loader = gateway_run._load_gateway_config
    try:
        gateway_run._load_gateway_config = lambda: {
            "display": {
                "platforms": {
                    "qqbot": {
                        "streaming": True,
                        "interim_assistant_messages": True,
                    }
                }
            }
        }
        c2c_cfg, _pause = runner._build_stream_consumer_config(
            SimpleNamespace(
                platform=Platform.QQBOT,
                chat_id="user-gate",
                chat_type="dm",
            ),
            scfg,
            gate_adapter,
            on_missing_cursor="raise",
        )
        assert c2c_cfg.transport == "auto"
        assert c2c_cfg.cursor == ""
        assert gate_adapter.supports_draft_streaming(
            chat_type="dm", chat_id="user-gate"
        )
        try:
            runner._build_stream_consumer_config(
                SimpleNamespace(
                    platform=Platform.QQBOT,
                    chat_id="group-gate",
                    chat_type="group",
                ),
                scfg,
                gate_adapter,
                on_missing_cursor="raise",
            )
        except RuntimeError as exc:
            assert "non-editable platform" in str(exc)
        else:
            raise AssertionError("QQ group unexpectedly bypassed edit-only gate")

        # QQ guild direct messages also use source.chat_type="dm", but the
        # adapter's authoritative route is "dm", not "c2c".
        try:
            runner._build_stream_consumer_config(
                SimpleNamespace(
                    platform=Platform.QQBOT,
                    chat_id="guild-dm-gate",
                    chat_type="dm",
                ),
                scfg,
                gate_adapter,
                on_missing_cursor="raise",
            )
        except RuntimeError as exc:
            assert "non-editable platform" in str(exc)
        else:
            raise AssertionError("QQ guild DM unexpectedly entered C2C lane")
        assert "guild-dm-gate" not in streaming_mod._native_lane_chats(
            gate_adapter
        )

        # Real in-process Runner combination: interim messages alone can cause
        # this builder call while both global and QQ streaming are false. The
        # native lane must remain disabled and upstream's non-editable gate
        # must reject the consumer.
        gateway_run._load_gateway_config = lambda: {
            "display": {
                "interim_assistant_messages": True,
                "platforms": {
                    "qqbot": {
                        "streaming": False,
                        "interim_assistant_messages": True,
                    }
                },
            }
        }
        disabled_gate_adapter = GatewayDummyAdapter()
        disabled_scfg = StreamingConfig(enabled=False, transport="auto")
        try:
            runner._build_stream_consumer_config(
                SimpleNamespace(
                    platform=Platform.QQBOT,
                    chat_id="user-interim-only",
                    chat_type="dm",
                ),
                disabled_scfg,
                disabled_gate_adapter,
                on_missing_cursor="raise",
            )
        except RuntimeError as exc:
            assert "non-editable platform" in str(exc)
        else:
            raise AssertionError("interim-only QQ unexpectedly opened native lane")
        assert not streaming_mod._native_lane_chats(disabled_gate_adapter)
        assert not disabled_gate_adapter.supports_draft_streaming(
            chat_type="dm", chat_id="user-interim-only"
        )

        # A platform-level opt-out also wins when top-level streaming remains
        # enabled. This exercises the complete resolved-setting precedence.
        try:
            runner._build_stream_consumer_config(
                SimpleNamespace(
                    platform=Platform.QQBOT,
                    chat_id="user-platform-opt-out",
                    chat_type="dm",
                ),
                StreamingConfig(enabled=True, transport="auto"),
                disabled_gate_adapter,
                on_missing_cursor="raise",
            )
        except RuntimeError as exc:
            assert "non-editable platform" in str(exc)
        else:
            raise AssertionError("QQ platform streaming opt-out was ignored")
        assert "user-platform-opt-out" not in streaming_mod._native_lane_chats(
            disabled_gate_adapter
        )

        # A live enabled -> disabled transition must revoke a lane selected
        # for an earlier turn on the same adapter.
        gateway_run._load_gateway_config = lambda: {
            "display": {"platforms": {"qqbot": {"streaming": True}}}
        }
        toggle_adapter = GatewayDummyAdapter()
        toggle_source = SimpleNamespace(
            platform=Platform.QQBOT,
            chat_id="toggle-user",
            chat_type="dm",
        )
        runner._build_stream_consumer_config(
            toggle_source,
            StreamingConfig(enabled=True, transport="auto"),
            toggle_adapter,
            on_missing_cursor="raise",
        )
        assert toggle_adapter.supports_draft_streaming(
            chat_type="dm", chat_id="toggle-user"
        )
        gateway_run._load_gateway_config = lambda: {
            "display": {"platforms": {"qqbot": {"streaming": False}}}
        }
        try:
            runner._build_stream_consumer_config(
                toggle_source,
                StreamingConfig(enabled=True, transport="auto"),
                toggle_adapter,
                on_missing_cursor="raise",
            )
        except RuntimeError as exc:
            assert "non-editable platform" in str(exc)
        else:
            raise AssertionError("disabled QQ lane unexpectedly stayed active")
        assert "toggle-user" not in streaming_mod._native_lane_chats(toggle_adapter)
        assert not toggle_adapter.supports_draft_streaming(
            chat_type="dm", chat_id="toggle-user"
        )
        toggle_adapter._last_msg_id["toggle-user"] = "toggle-typing"
        await toggle_adapter.send_typing("toggle-user")
        await toggle_adapter.send_typing("toggle-user")
        assert len(toggle_adapter.typing_calls) == 2

        # Revoking the lane must not discard an already-visible stream. Its
        # map entry keeps the passive-reply budget protected until close.
        gateway_run._load_gateway_config = lambda: {
            "display": {"platforms": {"qqbot": {"streaming": True}}}
        }
        open_toggle_adapter = GatewayDummyAdapter()
        open_toggle_source = SimpleNamespace(
            platform=Platform.QQBOT,
            chat_id="toggle-open-user",
            chat_type="dm",
        )
        runner._build_stream_consumer_config(
            open_toggle_source,
            StreamingConfig(enabled=True, transport="auto"),
            open_toggle_adapter,
            on_missing_cursor="raise",
        )
        await open_toggle_adapter.send_draft(
            "toggle-open-user",
            3401,
            "处理中",
            {"reply_to_message_id": "toggle-open-msg"},
        )
        gateway_run._load_gateway_config = lambda: {
            "display": {"platforms": {"qqbot": {"streaming": False}}}
        }
        try:
            runner._build_stream_consumer_config(
                open_toggle_source,
                StreamingConfig(enabled=True, transport="auto"),
                open_toggle_adapter,
                on_missing_cursor="raise",
            )
        except RuntimeError:
            pass
        else:
            raise AssertionError("open stream config disable bypassed gate")
        assert "toggle-open-user" not in streaming_mod._native_lane_chats(
            open_toggle_adapter
        )
        assert not open_toggle_adapter.supports_draft_streaming(
            chat_type="dm", chat_id="toggle-open-user"
        )
        assert streaming_mod._typing_budget_applies(
            open_toggle_adapter, "toggle-open-user"
        )
        closed_toggle = await open_toggle_adapter.abandon_open_draft(
            "toggle-open-user",
            "处理中",
            {"reply_to_message_id": "toggle-open-msg"},
        )
        assert closed_toggle.success

        disabled_gate_adapter._last_msg_id["user-interim-only"] = "typing-off"
        await disabled_gate_adapter.send_typing("user-interim-only")
        await disabled_gate_adapter.send_typing("user-interim-only")
        assert len(disabled_gate_adapter.typing_calls) == 2
    finally:
        gateway_run._load_gateway_config = original_config_loader

    # Exercise the actual Hermes native-draft consumer contract. The QQ
    # stream itself is the message: cumulative frames stay on one stream and
    # the consumer's notify=True final send becomes exactly one seal frame,
    # never a second ordinary QQ message.
    integrated = GatewayDummyAdapter()
    streaming_mod._mark_native_lane(integrated, "user-integrated")
    cfg = StreamConsumerConfig(
        transport="auto",
        chat_type="dm",
        edit_interval=0.01,
        buffer_threshold=1,
        cursor="",
    )
    consumer = GatewayStreamConsumer(
        integrated,
        "user-integrated",
        cfg,
        initial_reply_to_id="inbound-integrated",
    )
    async with anyio.create_task_group() as tg:
        tg.start_soon(consumer.run)
        consumer.on_delta("阶段一")
        await anyio.sleep(0.05)
        consumer.on_segment_break()
        await anyio.sleep(0.05)
        consumer.on_delta("，阶段二")
        await anyio.sleep(0.05)
        consumer.finish("阶段一，阶段二，完成")

    stream_bodies = [call[2] for call in integrated.api_calls]
    assert len(stream_bodies) >= 2
    assert [body["input_state"] for body in stream_bodies].count(10) == 1
    assert stream_bodies[-1]["content_raw"] == "阶段一，阶段二，完成"
    assert not integrated.normal_sends
    assert consumer.final_response_sent is True
    assert consumer.delivered_final_matches("阶段一，阶段二，完成") is True

    # A response beyond one QQ message must roll over as complete native
    # stream chunks. Generic Hermes overflow would emit an ordinary head and
    # then reuse the draft id with a shorter tail, violating replace-prefix.
    overflow = GatewayDummyAdapter()
    streaming_mod._mark_native_lane(overflow, "user-overflow")
    overflow_cfg = StreamConsumerConfig(
        transport="auto",
        chat_type="dm",
        edit_interval=0.01,
        buffer_threshold=1,
        cursor="",
    )
    overflow_consumer = GatewayStreamConsumer(
        overflow,
        "user-overflow",
        overflow_cfg,
        initial_reply_to_id="inbound-overflow",
    )
    overflow_final = "A" * 2000 + "B" * 2100 + "C" * 300
    async with anyio.create_task_group() as tg:
        tg.start_soon(overflow_consumer.run)
        overflow_consumer.on_delta("A" * 2000)
        await anyio.sleep(0.05)
        overflow_consumer.on_delta("B" * 2100)
        await anyio.sleep(0.05)
        overflow_consumer.on_delta("C" * 300)
        await anyio.sleep(0.05)
        overflow_consumer.finish(overflow_final)

    overflow_bodies = [call[2] for call in overflow.api_calls]
    assert not overflow.normal_sends
    assert sum(body["index"] == 0 for body in overflow_bodies) == 2
    sealed_chunks = [
        body["content_raw"]
        for body in overflow_bodies
        if body["input_state"] == 10
    ]
    assert len(sealed_chunks) == 2
    assert "".join(sealed_chunks) == overflow_final
    active_prefix = ""
    for body in overflow_bodies:
        if body["index"] == 0:
            active_prefix = ""
        assert body["content_raw"].startswith(active_prefix)
        active_prefix = body["content_raw"]
    assert overflow_consumer.final_response_sent is True
    assert overflow_consumer.delivered_final_matches(overflow_final) is True

    # The authoritative final can be the first payload that crosses the QQ
    # limit. It must roll over even when no committed overflow prefix exists
    # yet, rather than truncating the seal at 4000 characters.
    final_growth = DummyAdapter()
    final_growth_metadata = {"reply_to_message_id": "inbound-final-growth"}
    await final_growth.send_draft(
        "user-final-growth",
        5101,
        "D" * 3900,
        final_growth_metadata,
    )
    final_growth_text = "D" * 4100
    final_growth_result = await final_growth.send(
        "user-final-growth",
        final_growth_text,
        reply_to="inbound-final-growth",
        metadata={"notify": True, **final_growth_metadata},
    )
    assert final_growth_result.success
    assert not final_growth.normal_sends
    final_growth_seals = [
        call[2]["content_raw"]
        for call in final_growth.api_calls
        if call[2]["input_state"] == 10
    ]
    assert "".join(final_growth_seals) == final_growth_text

    # If the head was sealed but the new tail stream cannot open, the ordinary
    # fallback owns only the uncommitted suffix. Sending the complete final
    # would duplicate the already-visible 4000-character head.
    tail_failure = DummyAdapter()
    tail_failure.fail_tail_open_attempts = 2
    tail_failure_metadata = {"reply_to_message_id": "inbound-tail-failure"}
    tail_failure_text = "E" * 2000 + "F" * 2100
    await tail_failure.send_draft(
        "user-tail-failure",
        5102,
        "E" * 2000,
        tail_failure_metadata,
    )
    await tail_failure.send_draft(
        "user-tail-failure",
        5102,
        tail_failure_text,
        tail_failure_metadata,
    )
    tail_failure_result = await tail_failure.send(
        "user-tail-failure",
        tail_failure_text,
        reply_to="inbound-tail-failure",
        metadata={"notify": True, **tail_failure_metadata},
    )
    assert tail_failure_result.success
    assert [len(item[1]) for item in tail_failure.normal_sends] == [100]
    assert tail_failure.normal_sends[-1][1] == tail_failure_text[4000:]
    tail_failure_streams, _tail_failure_anchors = streaming_mod._stream_maps(
        tail_failure
    )
    assert not tail_failure_streams

    # A stale/cancelled consumer can close the same visible stream through
    # Hermes' real three-argument abandon_open_draft contract.
    cancelled = GatewayDummyAdapter()
    await cancelled.send_draft(
        "user-cancel",
        5001,
        "部分结果",
        {"reply_to_message_id": "inbound-cancel"},
    )
    closed = await cancelled.abandon_open_draft(
        "user-cancel",
        "部分结果",
        {"reply_to_message_id": "inbound-cancel"},
    )
    assert closed.success
    assert cancelled.api_calls[-1][2]["input_state"] == 10

    print("qq_c2c_stream_open_continue_seal=ok")
    print("qq_c2c_stream_seal_preserves_prefix=ok")
    print("qq_c2c_stream_parallel_dm_isolation=ok")
    print("qq_c2c_stream_nonfinal_send_isolation=ok")
    print("qq_c2c_stream_abandon_close=ok")
    print("qq_c2c_stream_fallback=ok")
    print("qq_c2c_stream_seal_retry=ok")
    print("qq_c2c_stream_safe_final_fallback_close=ok")
    print("qq_c2c_stream_seal_state_retained=ok")
    print("qq_c2c_stream_capacity_preserves_opened=ok")
    print("qq_c2c_disabled_typing_unchanged=ok")
    print("qq_c2c_interim_only_runner_stays_disabled=ok")
    print("qq_c2c_prerelease_version_fail_closed=ok")
    print("qq_c2c_typing_budget=ok")
    print("qq_c2c_gateway_stream_gate=ok")
    print("qq_c2c_gateway_stream_consumer=ok")
    print("qq_c2c_guild_dm_rejected=ok")
    print("qq_c2c_runtime_disable_revokes_lane=ok")
    print("qq_c2c_overflow_rollover=ok")
    print("qq_c2c_final_first_overflow_rollover=ok")
    print("qq_c2c_tail_open_failure_suffix_fallback=ok")


anyio.run(main)
