"""Recover Codex app-server imageGeneration items for gateway delivery.

Codex's built-in image tool completes as an ``imageGeneration`` item whose
``result`` is base64 image data.  Hermes 0.18.2 treats unknown Codex items as
opaque assistant notes and truncates their JSON, so no normal
``image_generate`` tool result reaches the gateway media auto-append logic.
Codex may then legitimately finish with an empty text answer because the image
itself was the answer.  The gateway's empty-response branch returns before it
scans tool results, leaving the generated image on disk but never sending it.

This module projects imageGeneration as the standard ``image_generate`` tool
pair, materializes the image under the canonical Hermes cache, and supplies a
MEDIA directive only when the Codex turn otherwise has no final text.
"""

from __future__ import annotations

import base64
import binascii
import functools
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

_PROJECTOR_MARKER = "_codex_image_generation_delivery_hotfix_wrapped"
_RUNTIME_MARKER = "_codex_empty_image_final_hotfix_wrapped"
_IMAGE_ID_RE = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_ENCODED_BYTES = 80 * 1024 * 1024


def _image_cache_dir() -> Path:
    override = os.getenv("HERMES_CODEX_IMAGE_CACHE_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    root = Path(os.getenv("HERMES_HOME", "~/.hermes")).expanduser()
    return root / "cache" / "images"


def _decode_image_result(value: Any) -> tuple[bytes, str] | None:
    if not isinstance(value, str) or not value.strip():
        return None
    encoded = value.strip()
    if encoded.startswith("data:"):
        header, separator, payload = encoded.partition(",")
        if not separator or ";base64" not in header.lower():
            return None
        encoded = payload
    if len(encoded) > _MAX_ENCODED_BYTES:
        raise ValueError("Codex imageGeneration result exceeds the safety limit")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Codex imageGeneration returned invalid base64") from exc
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return data, ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return data, ".jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return data, ".gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return data, ".webp"
    raise ValueError("Codex imageGeneration result is not a supported image")


def materialize_image_generation(item: dict[str, Any]) -> str | None:
    """Write one completed Codex image item to the Hermes image cache."""
    if str(item.get("status") or "").lower() not in {"", "completed"}:
        return None
    decoded = _decode_image_result(item.get("result"))
    if decoded is None:
        return None
    data, suffix = decoded
    raw_id = str(item.get("id") or "codex-image")
    safe_id = _IMAGE_ID_RE.sub("_", raw_id).strip("._") or "codex-image"
    target_dir = _image_cache_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"codex_{safe_id}{suffix}"
    if target.is_file() and target.stat().st_size == len(data):
        return str(target.resolve())

    fd, temporary = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target_dir)
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return str(target.resolve())


def _image_projection(item: dict[str, Any], image_path: str):
    from agent.transports.codex_event_projector import ProjectionResult

    item_id = str(item.get("id") or "codex-image")
    call_id = f"codex_image_generate_{_IMAGE_ID_RE.sub('_', item_id)}"
    revised_prompt = item.get("revisedPrompt")
    arguments = {"source": "codex_app_server.imageGeneration"}
    if isinstance(revised_prompt, str) and revised_prompt.strip():
        arguments["prompt"] = revised_prompt[:4000]
    assistant_message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": "image_generate",
                    "arguments": json.dumps(arguments, ensure_ascii=False),
                },
            }
        ],
    }
    tool_message = {
        "role": "tool",
        "tool_call_id": call_id,
        "tool_name": "image_generate",
        "content": json.dumps(
            {
                "success": True,
                "image": image_path,
                "source": "codex_app_server.imageGeneration",
            },
            ensure_ascii=False,
        ),
    }
    return ProjectionResult(
        messages=[assistant_message, tool_message], is_tool_iteration=True
    )


def wrap_project_opaque(original: Callable) -> Callable:
    @functools.wraps(original)
    def project_opaque(self, item: dict[str, Any], item_type: str):
        if item_type != "imageGeneration" or not isinstance(item, dict):
            return original(self, item, item_type)
        try:
            image_path = materialize_image_generation(item)
        except Exception as exc:
            logger.warning(
                "codex-app-server-phase-hotfix: image materialization failed: %s",
                exc,
            )
            return original(self, item, item_type)
        if not image_path:
            return original(self, item, item_type)
        logger.info(
            "codex-app-server-phase-hotfix: materialized Codex image %s",
            image_path,
        )
        return _image_projection(item, image_path)

    setattr(project_opaque, _PROJECTOR_MARKER, True)
    return project_opaque


def _media_path_from_messages(messages: list[dict[str, Any]]) -> str | None:
    tool_names: dict[str, str] = {}
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            call_id = str(call.get("id") or call.get("call_id") or "")
            function = call.get("function") if isinstance(call.get("function"), dict) else {}
            tool_name = str(function.get("name") or call.get("name") or "")
            if call_id and tool_name:
                tool_names[call_id] = tool_name
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") not in {"tool", "function"}:
            continue
        call_id = str(message.get("tool_call_id") or message.get("call_id") or "")
        if tool_names.get(call_id) != "image_generate":
            continue
        try:
            payload = json.loads(str(message.get("content") or ""))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or not payload.get("success"):
            continue
        image_path = payload.get("image")
        if isinstance(image_path, str) and Path(image_path).is_file():
            return str(Path(image_path).resolve())
    return None


def wrap_codex_runtime_turn(original: Callable) -> Callable:
    @functools.wraps(original)
    def run_turn(agent, **kwargs):
        messages = kwargs.get("messages")
        history_len = len(messages) if isinstance(messages, list) else 0
        result = original(agent, **kwargs)
        if not isinstance(result, dict) or str(result.get("final_response") or "").strip():
            return result
        result_messages = result.get("messages")
        if not isinstance(result_messages, list):
            return result
        current_turn = result_messages[history_len:] if len(result_messages) >= history_len else result_messages
        image_path = _media_path_from_messages(current_turn)
        if not image_path:
            return result
        patched = dict(result)
        patched["final_response"] = f"MEDIA:{image_path}"
        return patched

    setattr(run_turn, _RUNTIME_MARKER, True)
    return run_turn


def patch_codex_image_delivery() -> str:
    """Install projector and empty-final recovery patches idempotently."""
    from agent import codex_runtime
    from agent.transports.codex_event_projector import CodexEventProjector

    statuses: list[str] = []
    opaque = CodexEventProjector._project_opaque
    if getattr(opaque, _PROJECTOR_MARKER, False):
        statuses.append("projector already patched")
    else:
        CodexEventProjector._project_opaque = wrap_project_opaque(opaque)
        statuses.append("projector patched")

    runtime = codex_runtime.run_codex_app_server_turn
    if getattr(runtime, _RUNTIME_MARKER, False):
        statuses.append("runtime already patched")
    else:
        codex_runtime.run_codex_app_server_turn = wrap_codex_runtime_turn(runtime)
        statuses.append("runtime patched")
    return ", ".join(statuses)
