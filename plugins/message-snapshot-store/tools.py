"""Agent tools and a deterministic slash command for snapshot retrieval."""

from __future__ import annotations

import json
import shlex
from typing import Any

from .capture import materialize_snapshot
from .store import SnapshotStore

FILTER_PROPERTIES = {
    "snapshot_id": {"type": "integer", "description": "Exact local snapshot ID."},
    "message_id": {"type": "string", "description": "Exact platform message ID."},
    "platform": {"type": "string"},
    "chat_id": {"type": "string"},
    "chat_type": {"type": "string"},
    "sender_id": {"type": "string"},
    "event_type": {"type": "string"},
    "message_kind": {"type": "string"},
    "attachment_filename": {"type": "string"},
    "attachment_sha256": {"type": "string"},
    "attachment_remote_id": {"type": "string"},
    "attachment_url": {"type": "string"},
    "media_type": {"type": "string"},
    "field_path": {"type": "string", "description": "Exact flattened raw JSON path."},
    "value": {"type": "string", "description": "Exact raw JSON scalar value."},
    "from_time": {"type": "string", "description": "Inclusive ISO-8601 lower bound."},
    "to_time": {"type": "string", "description": "Inclusive ISO-8601 upper bound."},
    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
}

SEARCH_SCHEMA = {
    "name": "message_snapshot_search",
    "description": (
        "Search permanent message snapshots. Exact filters are hard constraints; "
        "free text uses hybrid exact, SQLite FTS5/BM25, substring, and fuzzy recall "
        "combined with reciprocal-rank fusion. Use this when a human asks to recall "
        "messages or media outside the short recent context."
    ),
    "parameters": {
        "type": "object",
        "properties": {"query": {"type": "string"}, **FILTER_PROPERTIES},
    },
}

GET_SCHEMA = {
    "name": "message_snapshot_get",
    "description": "Get one exact message snapshot including raw event payloads and attachment metadata.",
    "parameters": {
        "type": "object",
        "properties": {"identifier": {"type": "string", "description": "Snapshot ID or exact platform message ID."}},
        "required": ["identifier"],
    },
}

RESTORE_SCHEMA = {
    "name": "message_snapshot_restore",
    "description": (
        "Restore a snapshot manifest. In link mode this returns the original QQ media links "
        "without downloading bytes; in mirror mode it also materializes durable files."
    ),
    "parameters": {
        "type": "object",
        "properties": {"identifier": {"type": "string", "description": "Snapshot ID or exact platform message ID."}},
        "required": ["identifier"],
    },
}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def register_snapshot_interfaces(ctx, store: SnapshotStore) -> None:
    def search_handler(args: dict, **_: Any) -> str:
        values = dict(args or {})
        query = str(values.pop("query", "") or "")
        try:
            return _json({"results": store.hybrid_search(query, **values)})
        except Exception as exc:
            return _json({"error": f"snapshot search failed: {exc}"})

    def get_handler(args: dict, **_: Any) -> str:
        identifier = str((args or {}).get("identifier") or "").strip()
        if not identifier:
            return _json({"error": "identifier is required"})
        result = store.get_snapshot(identifier)
        return _json(result if result is not None else {"error": "snapshot not found"})

    async def restore_handler(args: dict, **_: Any) -> str:
        identifier = str((args or {}).get("identifier") or "").strip()
        if not identifier:
            return _json({"error": "identifier is required"})
        result = await materialize_snapshot(store, identifier)
        return _json(result if result is not None else {"error": "snapshot not found"})

    ctx.register_tool(
        name="message_snapshot_search",
        toolset="message_snapshot",
        schema=SEARCH_SCHEMA,
        handler=search_handler,
        description=SEARCH_SCHEMA["description"],
        emoji="🔎",
    )
    ctx.register_tool(
        name="message_snapshot_get",
        toolset="message_snapshot",
        schema=GET_SCHEMA,
        handler=get_handler,
        description=GET_SCHEMA["description"],
        emoji="🧾",
    )
    ctx.register_tool(
        name="message_snapshot_restore",
        toolset="message_snapshot",
        schema=RESTORE_SCHEMA,
        handler=restore_handler,
        is_async=True,
        description=RESTORE_SCHEMA["description"],
        emoji="♻️",
    )

    async def snapshot_command(raw_args: str) -> str:
        return await _handle_snapshot_command(store, raw_args)

    ctx.register_command(
        "message-snapshot",
        snapshot_command,
        description="Search, inspect, restore, or inspect permanent message snapshots.",
        args_hint="search|get|restore|stats ...",
    )


async def _handle_snapshot_command(store: SnapshotStore, raw_args: str) -> str:
    try:
        parts = shlex.split(raw_args or "")
    except ValueError as exc:
        return f"参数解析失败：{exc}"
    if not parts:
        return _help()
    action = parts.pop(0).lower()
    if action == "stats":
        return _json(store.stats())
    if action in {"get", "restore"}:
        if not parts:
            return f"用法：/message-snapshot {action} <snapshot_id|message_id>"
        result = (
            store.get_snapshot(parts[0])
            if action == "get"
            else await materialize_snapshot(store, parts[0])
        )
        return _json(result if result is not None else {"error": "snapshot not found"})
    if action in {"search", "find"}:
        filters: dict[str, Any] = {}
        terms = []
        valid = set(FILTER_PROPERTIES)
        for part in parts:
            if "=" in part:
                key, value = part.split("=", 1)
                if key in valid:
                    filters[key] = int(value) if key in {"snapshot_id", "limit"} and value.isdigit() else value
                    continue
            terms.append(part)
        return _json({"results": store.hybrid_search(" ".join(terms), **filters)})
    return _help()


def _help() -> str:
    return (
        "永久消息快照命令：\n"
        "/message-snapshot search <关键词> [chat_id=...] [sender_id=...] [message_kind=...]\n"
        "/message-snapshot search message_id=<精确ID>\n"
        "/message-snapshot search field_path=author.member_openid value=<精确值>\n"
        "/message-snapshot get <snapshot_id|message_id>\n"
        "/message-snapshot restore <snapshot_id|message_id>\n"
        "/message-snapshot stats"
    )
