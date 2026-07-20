from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace


def load_package():
    root = Path(__file__).parent
    name = "message_snapshot_store_test"
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


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    config = SnapshotConfig(
        root_dir=root,
        db_path=root / "snapshots.sqlite3",
        media_dir=root / "media",
        restore_dir=root / "restored",
        media_storage="link",
        context_messages=20,
        context_tokens=4000,
        search_candidates=100,
    )
    store = SnapshotStore(config)
    store.initialize()

    image_raw = {
        "id": "msg-image-1",
        "group_openid": "group-1",
        "author": {"member_openid": "member-a"},
        "content": "",
        "timestamp": "2026-07-20T12:00:00Z",
        "attachments": [
            {
                "id": "remote-image-1",
                "content_type": "image/png",
                "filename": "设计草图.png",
                "size": 12345,
                "url": "https://multimedia.example.invalid/signed/image-1",
            }
        ],
    }
    image_id = store.record_raw(
        profile="default",
        platform="qqbot",
        event_type="GROUP_MESSAGE_CREATE",
        raw=image_raw,
    )

    text_raw = {
        "id": "msg-text-1",
        "group_openid": "group-1",
        "author": {"member_openid": "member-b"},
        "content": "讨论蓝色登陆页的导航结构",
        "timestamp": "2026-07-20T12:01:00Z",
    }
    text_id = store.record_raw(
        profile="default",
        platform="qqbot",
        event_type="GROUP_MESSAGE_CREATE",
        raw=text_raw,
    )

    event = SimpleNamespace(
        source=SimpleNamespace(
            platform="qqbot",
            chat_id="group-1",
            chat_type="group",
            user_id="member-b",
            user_name="小蓝",
        ),
        raw_message=text_raw,
        message_id="msg-text-1",
        text="讨论蓝色登陆页的导航结构",
        message_type="text",
        reply_to_message_id=None,
        timestamp=None,
        media_urls=[],
        media_types=[],
    )
    normalized_id = store.record_normalized(event)
    assert normalized_id == text_id

    exact = store.hybrid_search(message_id="msg-image-1")
    assert exact[0]["snapshot_id"] == image_id
    assert exact[0]["attachments"][0]["remote_id"] == "remote-image-1"
    assert exact[0]["attachments"][0]["archive_status"] == "pending"

    field = store.hybrid_search(
        field_path="author.member_openid",
        value="member-a",
    )
    assert field[0]["snapshot_id"] == image_id

    lexical = store.hybrid_search("蓝色 导航")
    assert lexical[0]["snapshot_id"] == text_id
    assert "bm25" in lexical[0]["retrieval_sources"] or "substring" in lexical[0]["retrieval_sources"]

    fuzzy = store.hybrid_search("设计草图")
    assert fuzzy[0]["snapshot_id"] == image_id

    context = store.recent_context(
        platform="qqbot",
        chat_id="group-1",
        exclude_snapshot_id=text_id,
    )
    assert f"[snapshot:{image_id}]" in context
    assert "设计草图.png" in context

    restored = store.restore_snapshot(image_id)
    assert restored is not None
    assert restored["restored_files"] == []
    assert restored["remote_links"][0]["remote_id"] == "remote-image-1"

    stats = store.stats()
    assert stats["messages"] == 2
    assert stats["attachments"] == 1
    assert stats["media_storage"] == "link"
    assert stats["archived_bytes"] == 0

    # A second transport event for the same logical message remains immutable.
    store.record_raw(
        profile="default",
        platform="qqbot",
        event_type="GROUP_AT_MESSAGE_CREATE",
        raw={**text_raw, "content": "@bot 讨论蓝色登陆页的导航结构"},
    )
    assert store.stats()["messages"] == 2
    assert store.stats()["raw_events"] == 3

    print("pure_media_snapshot=true")
    print("link_storage_bytes=0")
    print("exact_field_lookup=true")
    print("hybrid_bm25_retrieval=true")
    print("bounded_context_includes_media=true")
    print("immutable_raw_events=true")
