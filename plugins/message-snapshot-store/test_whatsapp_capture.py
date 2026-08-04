from __future__ import annotations

import asyncio
import importlib.util
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace


def load_package():
    root = Path(__file__).parent
    name = "message_snapshot_whatsapp_test"
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
patch_capture = sys.modules[f"{pkg.__name__}.capture"].patch_whatsapp_snapshot_capture


class FakeWhatsAppAdapter:
    def __init__(self):
        self.events = []

    async def _build_message_event(self, data):
        # Model Hermes require_mention: passive traffic is visible to Baileys
        # but rejected before a MessageEvent reaches the agent.
        if not data.get("mentionedIds"):
            return None
        return SimpleNamespace(
            source=SimpleNamespace(
                platform="whatsapp",
                chat_id=data["chatId"],
                chat_type="group" if data.get("isGroup") else "dm",
                user_id=data["senderId"],
                user_name=data.get("senderName"),
            ),
            raw_message=data,
            message_id=data["messageId"],
            text=data.get("body", ""),
            message_type="text",
            reply_to_message_id=data.get("quotedMessageId"),
            timestamp=None,
            media_urls=list(data.get("mediaUrls") or []),
            media_types=[data.get("mime", "") for _ in data.get("mediaUrls") or []],
            channel_context=None,
        )

    async def handle_message(self, event):
        self.events.append(event)


async def main():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache_file = root / "doc_deadbeef_department-plan.pdf"
        cache_file.write_bytes(b"baileys-decrypted-media")
        store = SnapshotStore(
            SnapshotConfig(
                root_dir=root / "snapshots",
                db_path=root / "snapshots" / "snapshots.sqlite3",
                media_dir=root / "snapshots" / "media",
                restore_dir=root / "snapshots" / "restored",
                # QQ remains link-only, but WhatsApp must mirror decrypted
                # Baileys files because there is no durable plaintext URL.
                media_storage="link",
            )
        )
        store.initialize()
        patch_capture(FakeWhatsAppAdapter, store)
        patch_capture(FakeWhatsAppAdapter, store)
        adapter = FakeWhatsAppAdapter()

        passive = {
            "messageId": "wa-passive-media-1",
            "chatId": "120363000000000000@g.us",
            "senderId": "8613800000000@s.whatsapp.net",
            "senderName": "Michael",
            "isGroup": True,
            "body": "请保存部门方案",
            "hasMedia": True,
            "mediaType": "document",
            "mime": "application/pdf",
            "fileName": "department-plan.pdf",
            "nativeType": "documentMessage",
            "mediaUrls": [str(cache_file)],
            "mentionedIds": [],
            "timestamp": 1785837000,
        }
        assert await adapter._build_message_event(passive) is None
        snapshot = store.get_snapshot("wa-passive-media-1")
        assert snapshot is not None
        assert snapshot["platform"] == "whatsapp"
        assert snapshot["content"] == "请保存部门方案"
        attachment = snapshot["attachments"][0]
        assert attachment["filename"] == "department-plan.pdf"
        assert attachment["archive_status"] == "archived"
        archive_path = Path(attachment["archive_path"])
        assert archive_path.read_bytes() == b"baileys-decrypted-media"

        mentioned = {
            "messageId": "wa-mentioned-2",
            "chatId": passive["chatId"],
            "senderId": passive["senderId"],
            "senderName": "Michael",
            "isGroup": True,
            "body": "@bot 查找刚才的部门方案",
            "hasMedia": False,
            "mediaUrls": [],
            "mentionedIds": ["bot@s.whatsapp.net"],
            "timestamp": 1785837060,
        }
        event = await adapter._build_message_event(mentioned)
        assert event is not None
        await adapter.handle_message(event)
        assert len(adapter.events) == 1
        assert "department-plan.pdf" in adapter.events[0].channel_context
        assert "请保存部门方案" in adapter.events[0].channel_context

        found = store.hybrid_search("部门方案", platform="whatsapp")
        assert "wa-passive-media-1" in {item["message_id"] for item in found}
        cache_file.unlink()
        restored = store.restore_snapshot("wa-passive-media-1")
        assert restored is not None
        assert Path(restored["restored_files"][0]["path"]).read_bytes() == b"baileys-decrypted-media"
        assert store.stats()["messages"] == 2
        assert store.stats()["archived_attachments"] == 1

        print("whatsapp_pre_mention_capture=true")
        print("whatsapp_passive_context=true")
        print("whatsapp_hybrid_retrieval=true")
        print("whatsapp_baileys_media_mirrored=true")
        print("whatsapp_offline_restore=true")


asyncio.run(main())
