import importlib.util
import sys
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


class Result:
    success = True
    error = None


class DummyQQAdapter:
    async def connect(self):
        return None

    def _guess_chat_type(self, chat_id):
        return "c2c"

    async def _send_c2c_text(self, openid, content, reply_to=None, keyboard=None):
        return Result()

    async def _send_group_text(self, group_openid, content, reply_to=None, keyboard=None):
        self.seen_text_reply_to = reply_to
        return Result()

    async def _send_media(self, chat_id, media_source, file_type, kind, caption=None, reply_to=None, file_name=None):
        self.seen_reply_to = reply_to
        return Result()


async def main():
    mod._patch_plain_text_retry(DummyQQAdapter)
    mod._patch_media_caption_retry(DummyQQAdapter)
    adapter = DummyQQAdapter()
    adapter._last_msg_id = {"group-openid": "msg-123"}
    await adapter._send_group_text("group-openid", "hello")
    assert adapter.seen_text_reply_to is None, adapter.seen_text_reply_to
    await adapter._send_media("group-openid", "/tmp/a.docx", 4, "file")
    assert adapter.seen_reply_to is None, adapter.seen_reply_to
    await adapter._send_group_text("group-openid", "hello", reply_to="msg-explicit")
    assert adapter.seen_text_reply_to == "msg-explicit", adapter.seen_text_reply_to
    await adapter._send_media("group-openid", "/tmp/a.docx", 4, "file", reply_to="msg-explicit")
    assert adapter.seen_reply_to == "msg-explicit", adapter.seen_reply_to
    print("stale_text_reply_to=None")
    print("stale_media_reply_to=None")
    print("explicit_reply_to=msg-explicit")


anyio.run(main)
