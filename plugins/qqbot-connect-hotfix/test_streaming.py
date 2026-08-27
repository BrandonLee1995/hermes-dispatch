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
        self.successful_api_calls = []
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
        call = (method, path, dict(body))
        self.api_calls.append(call)
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
            data = {"id": f"stream-{self.stream_counter}"}
        else:
            data = {"id": body["stream_msg_id"]}
        self.successful_api_calls.append(call)
        return data

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


def assert_exact_final_ownership(adapter, target):
    """Every final character has exactly one successful visible owner."""

    sealed = [
        call[2]["content_raw"]
        for call in adapter.successful_api_calls
        if call[2]["input_state"] == 10
    ]
    streams, _anchors = streaming_mod._stream_maps(adapter)
    visible_open = [
        state.last_content
        for state in streams.values()
        if state.stream_msg_id and not state.sealed
    ]
    ordinary = [item[1] for item in adapter.normal_sends]
    assert "".join(sealed + visible_open + ordinary) == target


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

    # Hermes' completed commentary callback is an ordinary ``_interim_send``.
    # When the exact item is already the token-bounded terminal payload of the
    # same anchored native stream, that second carrier must be acknowledged
    # without creating another QQ bubble.
    interim_owned = DummyAdapter()
    interim_owned_metadata = {
        "reply_to_message_id": "inbound-interim-owned"
    }
    await interim_owned.send_draft(
        "user-interim-owned",
        1004,
        "第一段\nSTATUS",
        interim_owned_metadata,
    )
    owned_interim = await interim_owned.send(
        "user-interim-owned",
        "STATUS",
        reply_to="inbound-interim-owned",
        metadata={"_interim_send": True, **interim_owned_metadata},
    )
    assert owned_interim.success
    assert owned_interim.raw_response["qq_stream_owned_interim"] is True
    assert not interim_owned.normal_sends

    # A nonterminal occurrence is not ownership evidence; neither are a
    # word-internal suffix, a different inbound anchor, or a non-interim send.
    interim_nonterminal = DummyAdapter()
    await interim_nonterminal.send_draft(
        "user-interim-nonterminal",
        1005,
        "STATUS\n继续处理",
        {"reply_to_message_id": "inbound-interim-nonterminal"},
    )
    await interim_nonterminal.send(
        "user-interim-nonterminal",
        "STATUS",
        reply_to="inbound-interim-nonterminal",
        metadata={
            "_interim_send": True,
            "reply_to_message_id": "inbound-interim-nonterminal",
        },
    )
    assert interim_nonterminal.normal_sends[-1][1] == "STATUS"

    interim_word_suffix = DummyAdapter()
    await interim_word_suffix.send_draft(
        "user-interim-word-suffix",
        1006,
        "NOTSTATUS",
        {"reply_to_message_id": "inbound-interim-word-suffix"},
    )
    await interim_word_suffix.send(
        "user-interim-word-suffix",
        "STATUS",
        reply_to="inbound-interim-word-suffix",
        metadata={
            "_interim_send": True,
            "reply_to_message_id": "inbound-interim-word-suffix",
        },
    )
    assert interim_word_suffix.normal_sends[-1][1] == "STATUS"

    await interim_owned.send(
        "user-interim-owned",
        "STATUS",
        reply_to="different-anchor",
        metadata={
            "_interim_send": True,
            "reply_to_message_id": "different-anchor",
        },
    )
    await interim_owned.send(
        "user-interim-owned",
        "STATUS",
        reply_to="inbound-interim-owned",
        metadata={"non_conversational": True},
    )
    assert [item[1] for item in interim_owned.normal_sends] == [
        "STATUS",
        "STATUS",
    ]

    # The ownership check spans already-sealed rollover heads plus the open
    # tail, so a completed commentary at the 4000-character boundary does not
    # fall back to an oversized duplicate ordinary message.
    interim_overflow = DummyAdapter()
    interim_overflow_text = "X" * 4000 + "\nSTATUS"
    await interim_overflow.send_draft(
        "user-interim-overflow",
        1007,
        interim_overflow_text,
        {"reply_to_message_id": "inbound-interim-overflow"},
    )
    overflow_interim = await interim_overflow.send(
        "user-interim-overflow",
        interim_overflow_text,
        reply_to="inbound-interim-overflow",
        metadata={
            "_interim_send": True,
            "reply_to_message_id": "inbound-interim-overflow",
        },
    )
    assert overflow_interim.success
    assert not interim_overflow.normal_sends

    # Hermes currently omits the reply anchor from _send_commentary. A unique
    # same-chat terminal owner is safe to recover; two matching concurrent
    # streams are ambiguous and must not be guessed.
    interim_ambiguous = DummyAdapter()
    for draft_id, anchor in (
        (1008, "inbound-interim-a"),
        (1009, "inbound-interim-b"),
    ):
        await interim_ambiguous.send_draft(
            "user-interim-ambiguous",
            draft_id,
            "SAME STATUS",
            {"reply_to_message_id": anchor},
        )
    ambiguous_interim = await interim_ambiguous.send(
        "user-interim-ambiguous",
        "SAME STATUS",
        metadata={"_interim_send": True},
    )
    assert ambiguous_interim.success
    assert interim_ambiguous.normal_sends[-1][1] == "SAME STATUS"

    interim_unowned = DummyAdapter()
    unowned_interim = await interim_unowned.send(
        "user-interim-unowned",
        "NO STREAM",
        metadata={"_interim_send": True},
    )
    assert unowned_interim.success
    assert interim_unowned.normal_sends[-1][1] == "NO STREAM"

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

    # After one bounded close round fails, retry the already-visible composed
    # final in place. Do not emit an ordinary duplicate.
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
    assert not seal_degrade.normal_sends
    degrade_streams, _degrade_anchors = streaming_mod._stream_maps(seal_degrade)
    assert not degrade_streams
    assert seal_degrade.api_calls[-1][2]["input_state"] == 10
    assert seal_degrade.api_calls[-1][2]["content_raw"] == "处理中\n最终回退"
    assert_exact_final_ownership(seal_degrade, "处理中\n最终回退")

    # If both close rounds remain unavailable, the complete visible stream is
    # still a single owner and stays addressable for an explicit later retry.
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
    assert not seal_recover.normal_sends
    recover_streams, _recover_anchors = streaming_mod._stream_maps(seal_recover)
    assert 3103 in recover_streams
    assert_exact_final_ownership(seal_recover, "处理中\n最终回退")
    closed_after_failure = await seal_recover.abandon_open_draft(
        "user-seal-recover",
        "最终回退",
        {"reply_to_message_id": "msg-seal-recover"},
    )
    assert closed_after_failure.success
    assert 3103 not in recover_streams
    assert_exact_final_ownership(seal_recover, "处理中\n最终回退")

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

    # Codex app-server emits live deltas for a commentary item and then emits
    # the completed phase=commentary item through Hermes' interim callback.
    # Hermes marks that second carrier as ``_interim_send`` even though the
    # exact text is already visible in the native QQ stream. The connector
    # must keep the stream as the sole message owner instead of posting an
    # identical ordinary QQ bubble beside it.
    commentary_duplicate = GatewayDummyAdapter()
    streaming_mod._mark_native_lane(
        commentary_duplicate,
        "user-commentary-duplicate",
    )
    commentary_consumer = GatewayStreamConsumer(
        commentary_duplicate,
        "user-commentary-duplicate",
        cfg,
        initial_reply_to_id="inbound-commentary-duplicate",
    )
    commentary_text = "处理即将完成\nPR187_TERMINAL_ONCE"
    async with anyio.create_task_group() as tg:
        tg.start_soon(commentary_consumer.run)
        commentary_consumer.on_delta(commentary_text)
        await anyio.sleep(0.05)
        commentary_consumer.on_commentary(commentary_text)
        await anyio.sleep(0.05)
        commentary_consumer.finish("PR187_TERMINAL_ONCE")

    commentary_bodies = [call[2] for call in commentary_duplicate.api_calls]
    assert [body["input_state"] for body in commentary_bodies].count(10) == 1
    assert commentary_bodies[-1]["content_raw"] == commentary_text
    assert not commentary_duplicate.normal_sends, (
        commentary_bodies,
        commentary_duplicate.normal_sends,
    )
    assert commentary_consumer.final_response_sent is True
    assert commentary_consumer.delivered_final_matches(
        "PR187_TERMINAL_ONCE"
    ) is True

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
    assert_exact_final_ownership(final_growth, final_growth_text)

    # A full 4000-character commentary followed by an independent short final
    # must roll into another native message instead of silently dropping the
    # final at the old seal-body cap.
    independent_full = DummyAdapter()
    independent_full_metadata = {
        "reply_to_message_id": "inbound-independent-full"
    }
    independent_full_draft = "G" * 4000
    independent_full_target = independent_full_draft + "\nFINAL"
    await independent_full.send_draft(
        "user-independent-full",
        5103,
        independent_full_draft,
        independent_full_metadata,
    )
    independent_full_result = await independent_full.send(
        "user-independent-full",
        "FINAL",
        reply_to="inbound-independent-full",
        metadata={"notify": True, **independent_full_metadata},
    )
    assert independent_full_result.success
    assert not independent_full.normal_sends
    assert_exact_final_ownership(independent_full, independent_full_target)

    # The same composition is lossless when an independent final is larger
    # than the remaining capacity in a partially filled commentary stream.
    independent_growth = DummyAdapter()
    independent_growth_metadata = {
        "reply_to_message_id": "inbound-independent-growth"
    }
    independent_growth_draft = "H" * 3900
    independent_growth_final = "I" * 200
    independent_growth_target = (
        independent_growth_draft + "\n" + independent_growth_final
    )
    await independent_growth.send_draft(
        "user-independent-growth",
        5104,
        independent_growth_draft,
        independent_growth_metadata,
    )
    independent_growth_result = await independent_growth.send(
        "user-independent-growth",
        independent_growth_final,
        reply_to="inbound-independent-growth",
        metadata={"notify": True, **independent_growth_metadata},
    )
    assert independent_growth_result.success
    assert not independent_growth.normal_sends
    assert_exact_final_ownership(independent_growth, independent_growth_target)

    # If every rollover-head seal retry fails, the old 3900-character stream
    # remains the visible owner and the ordinary fallback receives only the
    # 201-character unseen suffix. A recovered close must not absorb that
    # suffix and duplicate it.
    head_seal_failure = DummyAdapter()
    head_seal_failure_metadata = {
        "reply_to_message_id": "inbound-head-seal-failure"
    }
    head_seal_failure_draft = "J" * 3900
    head_seal_failure_final = "K" * 200
    head_seal_failure_target = (
        head_seal_failure_draft + "\n" + head_seal_failure_final
    )
    await head_seal_failure.send_draft(
        "user-head-seal-failure",
        5105,
        head_seal_failure_draft,
        head_seal_failure_metadata,
    )
    head_seal_failure.fail_seal_attempts = len(
        streaming_mod._SEAL_RETRY_DELAYS
    )
    head_seal_failure_result = await head_seal_failure.send(
        "user-head-seal-failure",
        head_seal_failure_final,
        reply_to="inbound-head-seal-failure",
        metadata={"notify": True, **head_seal_failure_metadata},
    )
    assert head_seal_failure_result.success
    assert [len(item[1]) for item in head_seal_failure.normal_sends] == [201]
    assert head_seal_failure.normal_sends[0][1] == (
        "\n" + head_seal_failure_final
    )
    assert_exact_final_ownership(head_seal_failure, head_seal_failure_target)

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
    assert_exact_final_ownership(tail_failure, tail_failure_text)

    # Once a rollover tail is visible, a failed first close round must retry
    # that same tail rather than sending it again through the ordinary API.
    tail_seal_failure = DummyAdapter()
    tail_seal_failure_metadata = {
        "reply_to_message_id": "inbound-tail-seal-failure"
    }
    tail_seal_failure_text = "L" * 4100
    await tail_seal_failure.send_draft(
        "user-tail-seal-failure",
        5106,
        "L" * 3900,
        tail_seal_failure_metadata,
    )
    await tail_seal_failure.send_draft(
        "user-tail-seal-failure",
        5106,
        tail_seal_failure_text,
        tail_seal_failure_metadata,
    )
    tail_seal_failure.fail_seal_attempts = len(
        streaming_mod._SEAL_RETRY_DELAYS
    )
    tail_seal_failure_result = await tail_seal_failure.send(
        "user-tail-seal-failure",
        tail_seal_failure_text,
        reply_to="inbound-tail-seal-failure",
        metadata={"notify": True, **tail_seal_failure_metadata},
    )
    assert tail_seal_failure_result.success
    assert not tail_seal_failure.normal_sends
    tail_seal_streams, _tail_seal_anchors = streaming_mod._stream_maps(
        tail_seal_failure
    )
    assert not tail_seal_streams
    assert_exact_final_ownership(tail_seal_failure, tail_seal_failure_text)

    # A successful ordinary suffix fallback remains an immutable owner even
    # when every immediate recovery seal fails. A later abandon/close must
    # seal only the native text that was already visible, not absorb the
    # ordinary-owned suffix and display the final twice.
    delayed_close = DummyAdapter()
    delayed_close_metadata = {
        "reply_to_message_id": "inbound-delayed-close"
    }
    delayed_close_target = "处理中\nFINAL"
    await delayed_close.send_draft(
        "user-delayed-close",
        5107,
        "处理中",
        delayed_close_metadata,
    )
    delayed_close.fail_next_stream = True
    delayed_close.fail_seal_attempts = len(
        streaming_mod._SEAL_RETRY_DELAYS
    )
    delayed_close_result = await delayed_close.send(
        "user-delayed-close",
        "FINAL",
        reply_to="inbound-delayed-close",
        metadata={"notify": True, **delayed_close_metadata},
    )
    assert delayed_close_result.success
    assert [item[1] for item in delayed_close.normal_sends] == ["\nFINAL"]
    delayed_close_streams, _delayed_close_anchors = streaming_mod._stream_maps(
        delayed_close
    )
    assert 5107 in delayed_close_streams
    assert_exact_final_ownership(delayed_close, delayed_close_target)
    delayed_closed = await delayed_close.abandon_open_draft(
        "user-delayed-close",
        delayed_close_target,
        delayed_close_metadata,
    )
    assert delayed_closed.success
    assert 5107 not in delayed_close_streams
    assert_exact_final_ownership(delayed_close, delayed_close_target)

    # Once an ordinary fallback owns the suffix, late draft frames from the
    # completed consumer cannot put that immutable text back into the native
    # message. They are stale lifecycle events and must be harmless.
    late_frame = DummyAdapter()
    late_frame_metadata = {
        "reply_to_message_id": "inbound-late-frame"
    }
    late_frame_target = "处理中\nFINAL"
    await late_frame.send_draft(
        "user-late-frame",
        5113,
        "处理中",
        late_frame_metadata,
    )
    late_frame.fail_next_stream = True
    late_frame.fail_seal_attempts = len(streaming_mod._SEAL_RETRY_DELAYS)
    late_frame_result = await late_frame.send(
        "user-late-frame",
        "FINAL",
        reply_to="inbound-late-frame",
        metadata={"notify": True, **late_frame_metadata},
    )
    assert late_frame_result.success
    await late_frame.send_draft(
        "user-late-frame",
        5113,
        late_frame_target,
        late_frame_metadata,
    )
    assert_exact_final_ownership(late_frame, late_frame_target)
    late_frame_closed = await late_frame.abandon_open_draft(
        "user-late-frame",
        late_frame_target,
        late_frame_metadata,
    )
    assert late_frame_closed.success
    assert_exact_final_ownership(late_frame, late_frame_target)

    # A retried turn-final callback may close the retained native stream, but
    # it must not deliver or absorb the ordinary-owned suffix a second time.
    retried_final = DummyAdapter()
    retried_final_metadata = {
        "reply_to_message_id": "inbound-retried-final"
    }
    retried_final_target = "处理中\nFINAL"
    await retried_final.send_draft(
        "user-retried-final",
        5114,
        "处理中",
        retried_final_metadata,
    )
    retried_final.fail_next_stream = True
    retried_final.fail_seal_attempts = len(streaming_mod._SEAL_RETRY_DELAYS)
    first_final = await retried_final.send(
        "user-retried-final",
        "FINAL",
        reply_to="inbound-retried-final",
        metadata={"notify": True, **retried_final_metadata},
    )
    assert first_final.success
    second_final = await retried_final.send(
        "user-retried-final",
        "FINAL",
        reply_to="inbound-retried-final",
        metadata={"notify": True, **retried_final_metadata},
    )
    assert second_final.success
    assert [item[1] for item in retried_final.normal_sends] == ["\nFINAL"]
    assert_exact_final_ownership(retried_final, retried_final_target)

    # Independent finals are payloads, not substring searches. An earlier,
    # non-terminal occurrence of the same value does not own the final.
    repeated_final = DummyAdapter()
    repeated_final_metadata = {
        "reply_to_message_id": "inbound-repeated-final"
    }
    repeated_final_draft = "progress FINAL details"
    repeated_final_target = repeated_final_draft + "\nFINAL"
    await repeated_final.send_draft(
        "user-repeated-final",
        5108,
        repeated_final_draft,
        repeated_final_metadata,
    )
    repeated_final_result = await repeated_final.send(
        "user-repeated-final",
        "FINAL",
        reply_to="inbound-repeated-final",
        metadata={"notify": True, **repeated_final_metadata},
    )
    assert repeated_final_result.success
    assert_exact_final_ownership(repeated_final, repeated_final_target)

    # A true terminal copy already has one visible owner and must not be
    # appended again merely because the final callback repeats it.
    terminal_final = DummyAdapter()
    terminal_final_metadata = {
        "reply_to_message_id": "inbound-terminal-final"
    }
    terminal_final_target = "progress\nFINAL"
    await terminal_final.send_draft(
        "user-terminal-final",
        5109,
        terminal_final_target,
        terminal_final_metadata,
    )
    terminal_final_result = await terminal_final.send(
        "user-terminal-final",
        "FINAL",
        reply_to="inbound-terminal-final",
        metadata={"notify": True, **terminal_final_metadata},
    )
    assert terminal_final_result.success
    assert_exact_final_ownership(terminal_final, terminal_final_target)

    # Coincidental partial overlap and a matching substring inside a larger
    # terminal word are not ownership. Preserve the complete independent
    # final behind a message boundary in both cases.
    for draft_id, draft_text, final_text in (
        (5110, "status F", "FINAL"),
        (5111, "status NOTFINAL", "FINAL"),
    ):
        overlap = DummyAdapter()
        overlap_metadata = {
            "reply_to_message_id": f"inbound-overlap-{draft_id}"
        }
        overlap_target = draft_text + "\n" + final_text
        await overlap.send_draft(
            f"user-overlap-{draft_id}",
            draft_id,
            draft_text,
            overlap_metadata,
        )
        overlap_result = await overlap.send(
            f"user-overlap-{draft_id}",
            final_text,
            reply_to=overlap_metadata["reply_to_message_id"],
            metadata={"notify": True, **overlap_metadata},
        )
        assert overlap_result.success
        assert_exact_final_ownership(overlap, overlap_target)

    # A caller-supplied message boundary is authoritative; composition must
    # not insert a second newline before an independent final that already
    # starts with one.
    leading_boundary = DummyAdapter()
    leading_boundary_metadata = {
        "reply_to_message_id": "inbound-leading-boundary"
    }
    await leading_boundary.send_draft(
        "user-leading-boundary",
        5115,
        "progress",
        leading_boundary_metadata,
    )
    leading_boundary_result = await leading_boundary.send(
        "user-leading-boundary",
        "\nFINAL",
        reply_to="inbound-leading-boundary",
        metadata={"notify": True, **leading_boundary_metadata},
    )
    assert leading_boundary_result.success
    assert_exact_final_ownership(leading_boundary, "progress\nFINAL")

    # A cumulative authoritative final remains a replace, not an independent
    # append, when it explicitly extends the complete visible draft.
    cumulative_final = DummyAdapter()
    cumulative_final_metadata = {
        "reply_to_message_id": "inbound-cumulative-final"
    }
    await cumulative_final.send_draft(
        "user-cumulative-final",
        5112,
        "progress ",
        cumulative_final_metadata,
    )
    cumulative_final_result = await cumulative_final.send(
        "user-cumulative-final",
        "progress complete",
        reply_to="inbound-cumulative-final",
        metadata={"notify": True, **cumulative_final_metadata},
    )
    assert cumulative_final_result.success
    assert_exact_final_ownership(cumulative_final, "progress complete")

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
    print("qq_c2c_streamed_interim_carrier_dedup=ok")
    print("qq_c2c_interim_ownership_boundaries=ok")
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
    print("qq_c2c_streamed_commentary_single_carrier=ok")
    print("qq_c2c_guild_dm_rejected=ok")
    print("qq_c2c_runtime_disable_revokes_lane=ok")
    print("qq_c2c_overflow_rollover=ok")
    print("qq_c2c_final_first_overflow_rollover=ok")
    print("qq_c2c_independent_final_full_rollover=ok")
    print("qq_c2c_independent_final_growth_rollover=ok")
    print("qq_c2c_head_seal_failure_suffix_ownership=ok")
    print("qq_c2c_tail_open_failure_suffix_fallback=ok")
    print("qq_c2c_tail_seal_failure_no_duplicate=ok")
    print("qq_c2c_delayed_close_ordinary_ownership=ok")
    print("qq_c2c_ordinary_owned_late_frame_ignored=ok")
    print("qq_c2c_ordinary_owned_final_retry=ok")
    print("qq_c2c_nonterminal_repeated_final=ok")
    print("qq_c2c_terminal_final_single_owner=ok")
    print("qq_c2c_partial_overlap_not_ownership=ok")
    print("qq_c2c_leading_boundary_not_duplicated=ok")
    print("qq_c2c_cumulative_final_replace=ok")


anyio.run(main)
