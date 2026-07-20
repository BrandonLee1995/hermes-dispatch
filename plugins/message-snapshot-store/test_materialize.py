from __future__ import annotations

import asyncio
import importlib.util
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace


def load_package():
    root = Path(__file__).parent
    name = "message_snapshot_materialize_test"
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
materialize_snapshot = sys.modules[f"{pkg.__name__}.capture"].materialize_snapshot


async def main():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache_file = root / "hermes-cache-image.png"
        cache_file.write_bytes(b"snapshot-media-bytes")
        store = SnapshotStore(
            SnapshotConfig(
                root_dir=root / "snapshot-root",
                db_path=root / "snapshot-root" / "snapshots.sqlite3",
                media_dir=root / "snapshot-root" / "media",
                restore_dir=root / "snapshot-root" / "restored",
                media_storage="link",
            )
        )
        store.initialize()
        raw = {
            "id": "cached-media-message",
            "group_openid": "group-cache",
            "author": {"member_openid": "member-cache"},
            "attachments": [
                {
                    "id": "remote-cache-1",
                    "content_type": "image/png",
                    "filename": "cache.png",
                    "url": "https://multimedia.example.invalid/cache.png",
                }
            ],
        }
        store.record_raw(
            profile="default",
            platform="qqbot",
            event_type="GROUP_AT_MESSAGE_CREATE",
            raw=raw,
        )
        snapshot_id = store.record_normalized(
            SimpleNamespace(
                source=SimpleNamespace(
                    platform="qqbot",
                    chat_id="group-cache",
                    chat_type="group",
                    user_id="member-cache",
                    user_name=None,
                ),
                raw_message=raw,
                message_id="cached-media-message",
                text="",
                message_type="photo",
                reply_to_message_id=None,
                timestamp=None,
                media_urls=[str(cache_file)],
                media_types=["image/png"],
            )
        )
        before = store.get_snapshot(snapshot_id)
        assert before["attachments"][0]["archive_status"] == "cached"
        assert before["attachments"][0]["archive_path"] == ""

        restored = await materialize_snapshot(store, snapshot_id)
        assert restored is not None
        assert not restored["materialize_errors"]
        assert Path(restored["restored_files"][0]["path"]).read_bytes() == b"snapshot-media-bytes"
        assert store.stats()["archived_bytes"] == len(b"snapshot-media-bytes")

        print("link_mode_initial_copy=false")
        print("explicit_restore_materialized=true")
        print("content_addressed_archive=true")


asyncio.run(main())
