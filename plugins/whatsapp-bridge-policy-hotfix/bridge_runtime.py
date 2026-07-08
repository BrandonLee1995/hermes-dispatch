from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

SOURCE_BRIDGE_DIR: Final = Path("/opt/hermes/scripts/whatsapp-bridge")
DATA_BRIDGE_DIR: Final = Path("/opt/data/scripts/whatsapp-bridge")
RUNTIME_BRIDGE_DIR: Final = Path(
    "/opt/data/plugins/whatsapp-bridge-policy-hotfix/runtime/whatsapp-bridge"
)
PATCH_MARKER: Final = "whatsapp-bridge-policy-hotfix: group sender DM allowlist bypass"
GROUP_GATE_OLD: Final = (
    "        if (!matchesAllowedUser(senderId, ALLOWED_USERS, SESSION_DIR)) {\n"
)
GROUP_GATE_NEW: Final = (
    f"        // {PATCH_MARKER}; Python group_policy/group_allow_from gates groups.\n"
    "        if (!isGroup && !matchesAllowedUser(senderId, ALLOWED_USERS, SESSION_DIR)) {\n"
)


class BridgePatchError(RuntimeError):
    pass


def patched_bridge_source(source: str) -> str:
    if PATCH_MARKER in source:
        return source
    if GROUP_GATE_OLD not in source:
        raise BridgePatchError("WhatsApp bridge sender allowlist gate not found")
    return source.replace(GROUP_GATE_OLD, GROUP_GATE_NEW, 1)


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
    source_dir: Path = SOURCE_BRIDGE_DIR,
    runtime_dir: Path = RUNTIME_BRIDGE_DIR,
    data_bridge_dir: Path = DATA_BRIDGE_DIR,
) -> Path:
    configured_runtime = os.getenv("WHATSAPP_BRIDGE_HOTFIX_RUNTIME_DIR")
    if configured_runtime:
        runtime_dir = Path(configured_runtime)
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
