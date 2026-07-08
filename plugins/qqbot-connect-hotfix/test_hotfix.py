import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

import anyio


def load_plugin_module():
    path = Path("/opt/data/plugins/qqbot-connect-hotfix/__init__.py")
    if not path.exists():
        path = Path(__file__).with_name("__init__.py")
    spec = importlib.util.spec_from_file_location(
        "qqbot_connect_hotfix_test",
        path,
        submodule_search_locations=[str(path.parent)],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "qqbot_connect_hotfix_test"
    mod.__path__ = [str(path.parent)]
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


mod = load_plugin_module()

temp_dir = tempfile.TemporaryDirectory()
channel_directory_path = Path(temp_dir.name) / "channel_directory.json"
channel_directory_path.write_text(
    json.dumps(
        {
            "platforms": {
                "qqbot": [
                    {"id": "B279C1A461933B21DAFEE3263B8854A6", "type": "group"},
                ],
            },
        },
    ),
    encoding="utf-8",
)
os.environ["HERMES_CHANNEL_DIRECTORY"] = str(channel_directory_path)


class DummyAdapter:
    MAX_MESSAGE_LENGTH = 4000

    def __init__(self):
        self.calls = []

    def _next_msg_seq(self, key):
        return 42

    async def _api_request(self, method, path, body):
        self.calls.append((method, path, body))
        return {"id": "ok"}


class DummyDispatchAdapter:
    def __init__(self):
        self.created = []
        self.events = []
        self._app_id = "123456"

    def _create_task(self, coro):
        self.created.append(coro)
        return coro

    def _dispatch_payload(self, payload):
        self.events.append(("dispatch", payload))

    async def _on_message(self, event_type, d):
        self.events.append((event_type, d))


class DummyEvent:
    def __init__(self, raw_message):
        self.raw_message = raw_message
        self.channel_context = None


class DummyHandleAdapter:
    def __init__(self):
        self.events = []

    async def handle_message(self, event):
        self.events.append(event)
        return None


async def main():
    mod.register(None)
    from gateway.platforms.qqbot.adapter import QQAdapter

    adapter = QQAdapter.__new__(QQAdapter)
    adapter._chat_type_map = {}
    assert adapter._guess_chat_type("B279C1A461933B21DAFEE3263B8854A6") == "group"
    face_message = '<faceType=1,faceId="333",ext="x"><faceType=1,faceId="333">'
    normalized = QQAdapter._strip_at_mention(face_message)
    assert normalized == "用户在群里 @ 了你，并发送了 2 个 QQ 表情。请根据上下文做简短回应。"
    mixed = QQAdapter._strip_at_mention('@Momo 你好 <faceType=1,faceId="333">')
    assert mixed == "你好 <faceType=1,faceId=\"333\">"

    directory_type = mod._lookup_channel_directory_type("B279C1A461933B21DAFEE3263B8854A6")
    assert directory_type == "group", directory_type

    mod._patch_group_message_create_event(DummyDispatchAdapter)
    dispatch_adapter = DummyDispatchAdapter()
    await dispatch_adapter._on_message(
        "GROUP_MESSAGE_CREATE",
        {
            "id": "msg-0",
            "content": "普通上下文",
            "group_openid": "group-openid",
            "author": {"member_openid": "member-a"},
        },
    )
    assert dispatch_adapter.events == []
    dispatch_adapter._dispatch_payload(
        {
            "op": 0,
            "t": "GROUP_MESSAGE_CREATE",
            "d": {
                "id": "msg-1",
                "content": "@Momo hello",
                "group_openid": "group-openid",
                "author": {"member_openid": "member-b"},
            },
        }
    )
    await dispatch_adapter.created[0]
    assert dispatch_adapter.events[0][0] == "GROUP_AT_MESSAGE_CREATE"
    assert "普通上下文" in dispatch_adapter.events[0][1]["_qqbot_channel_context"]

    ignored_adapter = DummyDispatchAdapter()
    await ignored_adapter._on_message("GROUP_MESSAGE_CREATE", {"id": "msg-2", "content": "hello"})
    assert ignored_adapter.events == []

    old_env = {
        key: os.environ.get(key)
        for key in (
            "QQBOT_GROUP_CONTEXT_MESSAGES",
            "QQBOT_GROUP_CONTEXT_CHARS",
            "QQBOT_GROUP_CONTEXT_SUMMARY_CHARS",
        )
    }
    os.environ["QQBOT_GROUP_CONTEXT_MESSAGES"] = "3"
    os.environ["QQBOT_GROUP_CONTEXT_CHARS"] = "500"
    os.environ["QQBOT_GROUP_CONTEXT_SUMMARY_CHARS"] = "200"
    try:
        compact_adapter = DummyDispatchAdapter()
        for idx in range(6):
            await compact_adapter._on_message(
                "GROUP_MESSAGE_CREATE",
                {
                    "id": f"msg-c{idx}",
                    "content": f"普通上下文{idx}",
                    "group_openid": "compact-group",
                    "author": {"member_openid": f"member-{idx % 2}"},
                },
            )
        await compact_adapter._on_message(
            "GROUP_MESSAGE_CREATE",
            {
                "id": "msg-c-at",
                "content": "@Momo hello",
                "group_openid": "compact-group",
                "author": {"member_openid": "member-at"},
            },
        )
        compact_context = compact_adapter.events[0][1]["_qqbot_channel_context"]
        assert "[Recent group messages - compacted]" in compact_context
        assert "Earlier messages compacted: 3" in compact_context
        assert "普通上下文3" in compact_context
        assert "普通上下文5" in compact_context
    finally:
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    mod._patch_group_channel_context(DummyHandleAdapter)
    handle_adapter = DummyHandleAdapter()
    event = DummyEvent({"_qqbot_channel_context": "[Recent group messages]\n[member-a] 普通上下文"})
    await handle_adapter.handle_message(event)
    assert event.channel_context == "[Recent group messages]\n[member-a] 普通上下文"

    dummy = DummyAdapter()
    result = await mod._send_plain_text(
        dummy,
        "group",
        "B279C1A461933B21DAFEE3263B8854A6",
        "**hello**",
    )
    assert result.success
    method, request_path, body = dummy.calls[0]
    assert method == "POST"
    assert request_path == "/v2/groups/B279C1A461933B21DAFEE3263B8854A6/messages"
    assert body["msg_type"] == 0
    assert body["content"] == "**hello**"
    print("adapter_guess_B279=group")
    print("emoji_only_normalized=2")
    print("directory_type_B279=group")
    print("group_message_create_context=recent")
    print("plain_path=" + request_path)
    print("plain_msg_type=0")


anyio.run(main)
