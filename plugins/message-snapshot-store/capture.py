"""QQ raw-event capture and durable recent-context injection."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from collections import OrderedDict
from pathlib import Path
from typing import Any

from .store import SnapshotStore, canonical_json

logger = logging.getLogger(__name__)

_active_qq_adapter: Any = None
_RAW_CAPTURE_CACHE_SIZE = 2048


def _profile_name() -> str:
    try:
        from hermes_cli.profiles import get_active_profile_name

        return get_active_profile_name()
    except Exception:
        return os.getenv("HERMES_PROFILE", "default") or "default"


def patch_qq_snapshot_capture(QQAdapter, store: SnapshotStore) -> None:
    """Wrap QQ and expose a load-order-independent raw capture hook."""
    current_capture_hook = getattr(QQAdapter, "_message_snapshot_capture_raw", None)
    if not getattr(current_capture_hook, "_message_snapshot_capture_hook", False):

        async def _message_snapshot_capture_raw(self, event_type: str, raw: Any) -> int | None:
            """Persist one transport form once, even when nested wrappers see it twice."""
            global _active_qq_adapter
            _active_qq_adapter = self
            if not _is_message_event(event_type, raw):
                return None

            capture_key = _raw_capture_key(event_type, raw)
            captured = getattr(self, "_message_snapshot_raw_capture_results", None)
            if not isinstance(captured, OrderedDict):
                captured = OrderedDict()
                self._message_snapshot_raw_capture_results = captured
            if capture_key in captured:
                return captured[capture_key]

            # Mark before yielding to the SQLite worker so concurrent duplicate
            # delivery cannot start a second write for the same transport form.
            captured[capture_key] = None
            try:
                snapshot_id = await asyncio.to_thread(
                    store.record_raw,
                    profile=_profile_name(),
                    platform="qqbot",
                    event_type=str(event_type or ""),
                    raw=raw,
                )
                captured[capture_key] = snapshot_id
                captured.move_to_end(capture_key)
                while len(captured) > _RAW_CAPTURE_CACHE_SIZE:
                    captured.popitem(last=False)
                if store.config.media_storage == "mirror":
                    await _mirror_pending_qq_attachments(self, store, snapshot_id)
                return snapshot_id
            except Exception:
                captured.pop(capture_key, None)
                raise

        _message_snapshot_capture_raw._message_snapshot_capture_hook = True
        QQAdapter._message_snapshot_capture_raw = _message_snapshot_capture_raw

    current_on_message = QQAdapter._on_message
    if not getattr(current_on_message, "_message_snapshot_store_wrapped", False):

        async def _on_message(self, event_type: str, raw: Any) -> None:
            try:
                await self._message_snapshot_capture_raw(event_type, raw)
            except Exception:
                logger.exception("message-snapshot-store: raw QQ snapshot failed")

            result = await current_on_message(self, event_type, raw)
            return result

        _on_message.__name__ = getattr(current_on_message, "__name__", "_on_message")
        _on_message.__qualname__ = getattr(current_on_message, "__qualname__", "QQAdapter._on_message")
        _on_message._message_snapshot_store_wrapped = True
        QQAdapter._on_message = _on_message

    current_handle = QQAdapter.handle_message
    if not getattr(current_handle, "_message_snapshot_context_wrapped", False):

        async def handle_message(self, event) -> None:
            snapshot_id: int | None = None
            try:
                snapshot_id = await asyncio.to_thread(
                    store.record_normalized,
                    event,
                    _profile_name(),
                )
                source = getattr(event, "source", None)
                if str(getattr(source, "chat_type", "") or "") == "group":
                    context = await asyncio.to_thread(
                        store.recent_context,
                        platform="qqbot",
                        chat_id=str(getattr(source, "chat_id", "") or ""),
                        exclude_snapshot_id=snapshot_id,
                    )
                    if context:
                        event.channel_context = context
            except Exception:
                logger.exception("message-snapshot-store: normalized QQ snapshot failed")
            return await current_handle(self, event)

        handle_message.__name__ = getattr(current_handle, "__name__", "handle_message")
        handle_message.__qualname__ = getattr(current_handle, "__qualname__", "QQAdapter.handle_message")
        handle_message._message_snapshot_context_wrapped = True
        QQAdapter.handle_message = handle_message

    logger.info("message-snapshot-store: patched QQ raw capture and durable context")


def _raw_capture_key(event_type: str, raw: Any) -> tuple[str, str]:
    payload = raw if isinstance(raw, dict) else {"value": raw}
    raw_sha = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return str(event_type or ""), raw_sha


async def materialize_snapshot(store: SnapshotStore, identifier: str | int) -> dict[str, Any] | None:
    """Explicitly download/cache a snapshot's linked media, then restore it."""
    snapshot = await asyncio.to_thread(store.get_snapshot, identifier)
    if not snapshot:
        return None
    adapter = _active_qq_adapter
    errors: list[dict[str, Any]] = []
    for attachment in snapshot.get("attachments", []):
        ordinal = int(attachment.get("ordinal") or 0)
        archive_path = Path(str(attachment.get("archive_path") or ""))
        if archive_path.is_file():
            continue
        local_path = Path(str(attachment.get("local_path") or ""))
        if local_path.is_file():
            try:
                data = await asyncio.to_thread(local_path.read_bytes)
                await asyncio.to_thread(
                    store.archive_bytes,
                    snapshot_id=int(snapshot["snapshot_id"]),
                    ordinal=ordinal,
                    data=data,
                    content_type=str(attachment.get("content_type") or ""),
                    filename=str(attachment.get("filename") or local_path.name),
                )
                continue
            except Exception as exc:
                errors.append({"ordinal": ordinal, "stage": "local-cache", "error": str(exc)})

        url = str(attachment.get("remote_url") or "")
        client = getattr(adapter, "_http_client", None) if adapter is not None else None
        if not url or client is None:
            errors.append(
                {
                    "ordinal": ordinal,
                    "stage": "remote",
                    "error": "no active QQ connection or stored URL",
                }
            )
            continue
        try:
            from tools.url_safety import is_safe_url

            if not is_safe_url(url):
                raise ValueError("stored media URL is not allowed by URL safety policy")
            headers = adapter._qq_media_headers() if hasattr(adapter, "_qq_media_headers") else {}
            response = await client.get(url, timeout=30.0, headers=headers)
            response.raise_for_status()
            await asyncio.to_thread(
                store.archive_bytes,
                snapshot_id=int(snapshot["snapshot_id"]),
                ordinal=ordinal,
                data=response.content,
                content_type=str(attachment.get("content_type") or response.headers.get("content-type") or ""),
                filename=str(attachment.get("filename") or ""),
            )
        except Exception as exc:
            errors.append({"ordinal": ordinal, "stage": "remote", "error": str(exc)})

    result = await asyncio.to_thread(store.restore_snapshot, identifier)
    if result is not None:
        result["materialize_errors"] = errors
    return result


def _is_message_event(event_type: str, raw: Any) -> bool:
    if not isinstance(raw, dict):
        return False
    event_type = str(event_type or "").upper()
    return "MESSAGE" in event_type and event_type.endswith("CREATE")


async def _mirror_pending_qq_attachments(adapter, store: SnapshotStore, snapshot_id: int) -> None:
    client = getattr(adapter, "_http_client", None)
    if client is None:
        return
    try:
        from tools.url_safety import is_safe_url
    except ImportError:
        is_safe_url = lambda value: str(value).startswith("https://")

    headers = adapter._qq_media_headers() if hasattr(adapter, "_qq_media_headers") else {}
    for item in await asyncio.to_thread(store.pending_attachments, snapshot_id):
        ordinal = int(item["ordinal"])
        url = str(item.get("remote_url") or "")
        if not url or not is_safe_url(url):
            await asyncio.to_thread(store.mark_attachment_failed, snapshot_id, ordinal)
            continue
        try:
            response = await client.get(url, timeout=30.0, headers=headers)
            response.raise_for_status()
            await asyncio.to_thread(
                store.archive_bytes,
                snapshot_id=snapshot_id,
                ordinal=ordinal,
                data=response.content,
                content_type=str(item.get("content_type") or response.headers.get("content-type") or ""),
                filename=str(item.get("filename") or ""),
            )
        except Exception:
            await asyncio.to_thread(store.mark_attachment_failed, snapshot_id, ordinal)
