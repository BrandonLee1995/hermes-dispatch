import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import anyio

from gateway.platforms.base import BasePlatformAdapter
from gateway.config import StreamingConfig
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

    def _guess_chat_type(self, chat_id):
        return "group" if str(chat_id).startswith("group-") else "c2c"

    def _next_msg_seq(self, key):
        return 73

    async def _api_request(self, method, path, body):
        self.api_calls.append((method, path, dict(body)))
        if self.fail_next_stream:
            self.fail_next_stream = False
            raise RuntimeError("stream unavailable")
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
    assert status == "QQ C2C native streaming patched"
    assert mod._patch_qq_c2c_streaming(DummyAdapter).endswith("already patched")

    adapter = DummyAdapter()
    assert adapter.supports_draft_streaming(chat_type="dm", chat_id="user-1")
    assert not adapter.supports_draft_streaming(
        chat_type="group", chat_id="group-1"
    )
    assert adapter.stream_is_message_for_chat("user-1")
    assert not adapter.stream_is_message_for_chat("group-1")
    rejected_group = await adapter.send_draft(
        "group-1", 999, "不应发送", {"reply_to_message_id": "group-msg"}
    )
    assert not rejected_group.success
    assert not adapter.api_calls

    metadata = {"reply_to_message_id": "inbound-1"}
    first = await adapter.send_draft("user-1", 1001, "正在读取", metadata)
    second = await adapter.send_draft("user-1", 1001, "正在读取知识库", metadata)
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
    assert adapter.api_calls[-1][2]["content_raw"] == "最终答案"
    assert not adapter.normal_sends

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

    # Typing is bounded to one passive input_notify per inbound msg_id.
    typing = DummyAdapter()
    typing._last_msg_id["user-t"] = "typing-1"
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
    c2c_cfg, _pause = runner._build_stream_consumer_config(
        SimpleNamespace(platform="qqbot", chat_id="user-gate", chat_type="dm"),
        scfg,
        gate_adapter,
        on_missing_cursor="raise",
    )
    assert c2c_cfg.transport == "auto"
    assert c2c_cfg.cursor == ""
    try:
        runner._build_stream_consumer_config(
            SimpleNamespace(
                platform="qqbot", chat_id="group-gate", chat_type="group"
            ),
            scfg,
            gate_adapter,
            on_missing_cursor="raise",
        )
    except RuntimeError as exc:
        assert "non-editable platform" in str(exc)
    else:
        raise AssertionError("QQ group unexpectedly bypassed edit-only gate")

    # Exercise the actual Hermes native-draft consumer contract. The QQ
    # stream itself is the message: cumulative frames stay on one stream and
    # the consumer's notify=True final send becomes exactly one seal frame,
    # never a second ordinary QQ message.
    integrated = GatewayDummyAdapter()
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
    print("qq_c2c_stream_parallel_dm_isolation=ok")
    print("qq_c2c_stream_nonfinal_send_isolation=ok")
    print("qq_c2c_stream_abandon_close=ok")
    print("qq_c2c_stream_fallback=ok")
    print("qq_c2c_typing_budget=ok")
    print("qq_c2c_gateway_stream_gate=ok")
    print("qq_c2c_gateway_stream_consumer=ok")


anyio.run(main)
