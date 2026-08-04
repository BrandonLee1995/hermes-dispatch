from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

LEGACY_SOURCE_BRIDGE_DIR: Final = Path("/opt/hermes/scripts/whatsapp-bridge")
PATCH_MARKER: Final = "whatsapp-bridge-policy-hotfix: group sender DM allowlist bypass"
GROUP_GATE_REPLACEMENTS: Final = (
    (
        "        if (WHATSAPP_DM_POLICY !== 'pairing' && "
        "!matchesAllowedUser(senderId, ALLOWED_USERS, SESSION_DIR)) {\n",
        f"        // {PATCH_MARKER}; Python group_policy/group_allow_from gates groups.\n"
        "        if (!isGroup && WHATSAPP_DM_POLICY !== 'pairing' && "
        "!matchesAllowedUser(senderId, ALLOWED_USERS, SESSION_DIR)) {\n",
    ),
    (
        "        if (!matchesAllowedUser(senderId, ALLOWED_USERS, SESSION_DIR)) {\n",
        f"        // {PATCH_MARKER}; Python group_policy/group_allow_from gates groups.\n"
        "        if (!isGroup && !matchesAllowedUser(senderId, ALLOWED_USERS, SESSION_DIR)) {\n",
    ),
)


class BridgePatchError(RuntimeError):
    pass


def patched_bridge_source(source: str) -> str:
    if PATCH_MARKER in source:
        return source
    for old, new in GROUP_GATE_REPLACEMENTS:
        if old in source:
            return source.replace(old, new, 1)
    raise BridgePatchError("WhatsApp bridge sender allowlist gate not found")


def _env_path(name: str) -> Path | None:
    raw = os.getenv(name, "").strip()
    return Path(raw).expanduser() if raw else None


def resolve_hermes_home() -> Path:
    configured = _env_path("HERMES_HOME")
    if configured is not None:
        return configured
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home()).expanduser()
    except ImportError:
        return Path.home() / ".hermes"


def resolve_source_bridge_dir() -> Path:
    configured = _env_path("WHATSAPP_BRIDGE_HOTFIX_SOURCE_DIR")
    if configured is not None:
        return configured
    try:
        from gateway.platforms.whatsapp_common import resolve_whatsapp_bridge_dir

        return Path(resolve_whatsapp_bridge_dir()).expanduser()
    except ImportError:
        hermes_home = resolve_hermes_home()
        candidates = (
            hermes_home / "hermes-agent" / "scripts" / "whatsapp-bridge",
            hermes_home / "scripts" / "whatsapp-bridge",
            LEGACY_SOURCE_BRIDGE_DIR,
        )
        return next((path for path in candidates if path.exists()), candidates[0])


def resolve_runtime_bridge_dir(plugin_dir: Path | None = None) -> Path:
    configured = _env_path("WHATSAPP_BRIDGE_HOTFIX_RUNTIME_DIR")
    if configured is not None:
        return configured
    root = plugin_dir or Path(__file__).resolve().parent
    return root / "runtime" / "whatsapp-bridge"


def resolve_data_bridge_dir(hermes_home: Path | None = None) -> Path:
    configured = _env_path("WHATSAPP_BRIDGE_HOTFIX_DATA_BRIDGE_DIR")
    if configured is not None:
        return configured
    return (hermes_home or resolve_hermes_home()) / "scripts" / "whatsapp-bridge"


def _copy_bridge_support_files(source_dir: Path, runtime_dir: Path) -> None:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    for path in source_dir.iterdir():
        if path.name == "bridge.js" or path.name == "node_modules":
            continue
        if path.is_file() and path.suffix in {".js", ".json"}:
            shutil.copy2(path, runtime_dir / path.name)


def _link_node_modules(source_dir: Path, runtime_dir: Path) -> None:
    source_node_modules = source_dir / "node_modules"
    runtime_node_modules = runtime_dir / "node_modules"
    if not source_node_modules.exists() or runtime_node_modules.exists():
        return
    try:
        runtime_node_modules.symlink_to(source_node_modules, target_is_directory=True)
    except OSError as exc:
        logger.warning(
            "whatsapp-bridge-policy-hotfix: could not symlink node_modules: %s",
            exc,
        )


def _patch_bridge_file(path: Path) -> None:
    if not path.exists():
        return
    source = path.read_text(encoding="utf-8")
    patched = patched_bridge_source(source)
    if patched != source:
        path.write_text(patched, encoding="utf-8")


def install_runtime_bridge(
    source_dir: Path | None = None,
    runtime_dir: Path | None = None,
    data_bridge_dir: Path | None = None,
) -> Path:
    source_dir = source_dir or resolve_source_bridge_dir()
    runtime_dir = runtime_dir or resolve_runtime_bridge_dir()
    data_bridge_dir = data_bridge_dir or resolve_data_bridge_dir()
    if source_dir.resolve() == runtime_dir.resolve():
        raise BridgePatchError("WhatsApp source and runtime bridge directories must differ")
    source_bridge = source_dir / "bridge.js"
    if not source_bridge.exists():
        raise BridgePatchError(f"WhatsApp bridge script missing: {source_bridge}")

    _copy_bridge_support_files(source_dir, runtime_dir)
    _link_node_modules(source_dir, runtime_dir)

    patched = patched_bridge_source(source_bridge.read_text(encoding="utf-8"))
    runtime_bridge = runtime_dir / "bridge.js"
    runtime_bridge.write_text(patched, encoding="utf-8")

    if os.getenv("WHATSAPP_BRIDGE_HOTFIX_PATCH_DATA_BRIDGE", "1").strip() != "0":
        _patch_bridge_file(data_bridge_dir / "bridge.js")

    return runtime_bridge
