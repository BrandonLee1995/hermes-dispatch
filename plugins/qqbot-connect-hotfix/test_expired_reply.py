import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import anyio


def load_plugin_module():
    path = Path("/opt/data/plugins/qqbot-connect-hotfix/__init__.py")
    if not path.exists():
        path = Path(__file__).with_name("__init__.py")
    spec = importlib.util.spec_from_file_location(
        "qqbot_connect_expired_reply_test",
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
    def __init__(self):
        self.calls = []

    async def _send_c2c_text(
        self, target_id, content, reply_to=None, keyboard=None
    ):
        self.calls.append(("c2c", target_id, content, reply_to, keyboard))
        if reply_to:
            raise RuntimeError("回复消息msg_id已过期")
        return SimpleNamespace(success=True, message_id="c2c-standalone")

    async def _send_group_text(
        self, target_id, content, reply_to=None, keyboard=None
    ):
        self.calls.append(("group", target_id, content, reply_to, keyboard))
        if reply_to:
            raise RuntimeError("QQ Bot API error: message_id expired")
        return SimpleNamespace(success=True, message_id="group-standalone")

    async def _send_guild_text(self, target_id, content, reply_to=None):
        self.calls.append(("guild", target_id, content, reply_to, None))
        if reply_to:
            raise RuntimeError("QQ Bot API error: message id expiration")
        return SimpleNamespace(success=True, message_id="guild-standalone")


class UnrelatedErrorAdapter(DummyAdapter):
    async def _send_group_text(
        self, target_id, content, reply_to=None, keyboard=None
    ):
        self.calls.append(("group", target_id, content, reply_to, keyboard))
        raise RuntimeError("QQ Bot API error: forbidden")


class FallbackFailureAdapter(DummyAdapter):
    async def _send_group_text(
        self, target_id, content, reply_to=None, keyboard=None
    ):
        self.calls.append(("group", target_id, content, reply_to, keyboard))
        if reply_to:
            raise RuntimeError("msg_id expired")
        raise RuntimeError("standalone rate limited")


async def main():
    mod._patch_expired_reply_fallback(DummyAdapter)
    # Registration is idempotent and must not add a second retry layer.
    mod._patch_expired_reply_fallback(DummyAdapter)

    keyboard = object()
    adapter = DummyAdapter()
    result = await adapter._send_group_text(
        "group-1", "approve?", "expired-1", keyboard
    )
    assert result.success
    assert adapter.calls == [
        ("group", "group-1", "approve?", "expired-1", keyboard),
        ("group", "group-1", "approve?", None, keyboard),
    ]

    c2c = DummyAdapter()
    result = await c2c._send_c2c_text("user-1", "done", "expired-2")
    assert result.success
    assert [call[3] for call in c2c.calls] == ["expired-2", None]

    guild = DummyAdapter()
    result = await guild._send_guild_text("channel-1", "done", "expired-3")
    assert result.success
    assert [call[3] for call in guild.calls] == ["expired-3", None]

    mod._patch_expired_reply_fallback(UnrelatedErrorAdapter)
    unrelated = UnrelatedErrorAdapter()
    try:
        await unrelated._send_group_text("group-2", "done", "anchor")
    except RuntimeError as exc:
        assert "forbidden" in str(exc)
    else:
        raise AssertionError("unrelated errors must not trigger standalone retry")
    assert len(unrelated.calls) == 1

    mod._patch_expired_reply_fallback(FallbackFailureAdapter)
    failed = FallbackFailureAdapter()
    try:
        await failed._send_group_text("group-3", "done", "expired-4")
    except RuntimeError as exc:
        text = str(exc)
        assert "standalone rate limited" in text
        assert "msg_id expired" in text
    else:
        raise AssertionError("fallback failure must preserve both diagnostics")

    for message in (
        "回复消息msg_id已过期",
        "msg_id expired",
        "message_id has expired",
        "message id expiration",
    ):
        assert mod._is_expired_reply_error(message), message
    for message in ("msg_id missing", "message id invalid", "request expired"):
        assert not mod._is_expired_reply_error(message), message

    print("expired_reply_group_fallback=ok")
    print("expired_reply_c2c_fallback=ok")
    print("expired_reply_guild_fallback=ok")
    print("expired_reply_keyboard_preserved=ok")
    print("expired_reply_diagnostics=ok")


anyio.run(main)
