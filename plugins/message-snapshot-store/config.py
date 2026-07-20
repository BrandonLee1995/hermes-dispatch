"""Configuration for the message snapshot store."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


@dataclass(frozen=True)
class SnapshotConfig:
    root_dir: Path
    db_path: Path
    media_dir: Path
    restore_dir: Path
    media_storage: str = "link"
    context_messages: int = 20
    context_tokens: int = 4000
    search_candidates: int = 200

    @classmethod
    def from_env(cls) -> "SnapshotConfig":
        hermes_home = Path(os.getenv("HERMES_HOME", "~/.hermes")).expanduser()
        root = Path(
            os.getenv(
                "MESSAGE_SNAPSHOT_ROOT",
                str(hermes_home / "message-snapshots"),
            )
        ).expanduser()
        return cls(
            root_dir=root,
            db_path=Path(os.getenv("MESSAGE_SNAPSHOT_DB", str(root / "snapshots.sqlite3"))).expanduser(),
            media_dir=Path(os.getenv("MESSAGE_SNAPSHOT_MEDIA_DIR", str(root / "media"))).expanduser(),
            restore_dir=Path(os.getenv("MESSAGE_SNAPSHOT_RESTORE_DIR", str(root / "restored"))).expanduser(),
            media_storage=_storage_mode(os.getenv("MESSAGE_SNAPSHOT_MEDIA_STORAGE", "link")),
            context_messages=_bounded_int("MESSAGE_SNAPSHOT_CONTEXT_MESSAGES", 20, 1, 100),
            context_tokens=_bounded_int("MESSAGE_SNAPSHOT_CONTEXT_TOKENS", 4000, 256, 32000),
            search_candidates=_bounded_int("MESSAGE_SNAPSHOT_SEARCH_CANDIDATES", 200, 20, 2000),
        )


def _storage_mode(value: str) -> str:
    normalized = str(value or "link").strip().lower()
    return normalized if normalized in {"link", "mirror"} else "link"
