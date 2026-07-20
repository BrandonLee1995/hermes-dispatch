from __future__ import annotations

import asyncio
import importlib.util
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace


def load_package():
    root = Path(__file__).parent
    name = "message_snapshot_capture_test"
    spec = importlib.util.spec_from_file_location(
        name,
        root / "__init__.py",
        submodule_search_locations=[str(root)],
    )
    module = importlib.util.module_from_spec(spec)
    module.__package__ = name
    module.__path__ = [str(root)]
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


pkg = load_package()
SnapshotConfig = sys.modules[f"{pkg.__name__}.config"].SnapshotConfig
SnapshotStore = sys.modules[f"{pkg.__name__}.store"].SnapshotStore
patch_capture = sys.modules[f"{pkg.__name__}.capture"].patch_qq_snapshot_capture


class FakeQQAdapter:
    def __init__(self):
        self.events = []

    async def _on_message(self, event_type, raw):
        # Mimic the QQ hotfix: non-mention GROUP_MESSAGE_CREATE is observed but
        # not routed to the agent, while GROUP_AT_MESSAGE_CREATE is normalized.
        if event_type != "GROUP_AT_MESSAGE_CREATE":
            return None
        event = SimpleNamespace(
            source=SimpleNamespace(
                platform="qqbot",
                chat_id=raw["group_openid"],
                chat_type="group",
                user_id=raw["author"]["member_openid"],
                user_name=None,
            ),
            raw_message=raw,
            message_id=raw["id"],
            text=raw.get("content", ""),
            message_type="text",
            reply_to_message_id=None,
            timestamp=None,
            media_urls=[],
            media_types=[],
            channel_context=None,
        )
        await self.handle_message(event)

    async def handle_message(self, event):
        self.events.append(event)


async def main():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = SnapshotStore(
            SnapshotConfig(
                root_dir=root,
                db_path=root / "snapshots.sqlite3",
                media_dir=root / "media",
                restore_dir=root / "restored",
                media_storage="link",
            )
        )
        store.initialize()
        patch_capture(FakeQQAdapter, store)
        patch_capture(FakeQQAdapter, store)

        # Reproduce the problematic load order: QQ's routing hotfix is the
        # outer wrapper and returns before the snapshot plugin's _on_message
        # wrapper. The explicit hook must still persist the passive event.
        snapshot_on_message = FakeQQAdapter._on_message

        async def qq_hotfix_outer(self, event_type, raw):
            if event_type == "GROUP_MESSAGE_CREATE":
                await self._message_snapshot_capture_raw(event_type, raw)
                return None
            return await snapshot_on_message(self, event_type, raw)

        FakeQQAdapter._on_message = qq_hotfix_outer
        adapter = FakeQQAdapter()

        passive_media = {
            "id": "media-only",
            "group_openid": "group-live",
            "author": {"member_openid": "member-media"},
            "content": "",
            "attachments": [
                {
                    "id": "qq-file-1",
                    "content_type": "application/pdf",
                    "filename": "history.pdf",
                    "url": "https://multimedia.example.invalid/file-1",
                }
            ],
        }
        await adapter._on_message("GROUP_MESSAGE_CREATE", passive_media)
        await adapter._message_snapshot_capture_raw("GROUP_MESSAGE_CREATE", passive_media)
        assert adapter.events == []
        assert store.stats()["messages"] == 1
        assert store.stats()["raw_events"] == 1

        await adapter._on_message(
            "GROUP_AT_MESSAGE_CREATE",
            {
                "id": "mention-1",
                "group_openid": "group-live",
                "author": {"member_openid": "member-human"},
                "content": "@bot 恢复刚才的文件",
            },
        )
        assert len(adapter.events) == 1
        context = adapter.events[0].channel_context
        assert "history.pdf" in context
        assert "[snapshot:" in context
        stats = store.stats()
        assert stats["messages"] == 2
        assert stats["attachments"] == 1
        assert stats["archived_bytes"] == 0

        print("non_text_group_event_captured=true")
        print("media_link_context_recovered=true")
        print("capture_patch_idempotent=true")
        print("capture_load_order_independent=true")


asyncio.run(main())
