#!/usr/bin/env python3
"""Install owner-owned, removable CLI/domain policy without touching Hermes core."""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import sys
import tempfile

SOURCE = Path(__file__).resolve().parents[1] / "ops/macos_launchd.py"
spec = importlib.util.spec_from_file_location("dispatch_launchd", SOURCE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def owned(path):
    if path.is_symlink() or path.exists() and path.stat().st_uid != os.getuid():
        raise ValueError("refusing a symlink or a file owned by another user")


def atomic(path, data, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    owned(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=".dispatch-launchd-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def install(root, launcher):
    if sys.platform != "darwin":
        return {"status": "not-applicable"}
    owned(root); owned(launcher)
    sys.path.insert(0, str(root / "hermes-agent"))
    from hermes_cli import gateway
    required = ("_probe_launchd_domain_for_label", "generate_launchd_plist", "_launchctl_bootstrap",
                "_launchd_fallback_to_detached", "launchd_stop", "launchd_restart", "run_gateway",
                "_wait_for_launchd_service_pid")
    if any(not callable(getattr(gateway, name, None)) for name in required):
        raise ValueError("unsupported upstream Gateway API; refusing to install a partial CLI guard")
    text = launcher.read_text()
    runtime = root / "scripts/dispatch-macos-launchd.py"
    old = [str(root / "hermes-agent/hermes"), str(root / "scripts/hermes-keyring-launchd.py"), str(runtime)]
    if not text.startswith(("#!/usr/bin/env bash\n", "#!/bin/bash\n", "#!/bin/sh\n")) or not any('"' + value + '"' in text for value in old):
        raise ValueError("unsupported Hermes launcher; refusing to overwrite unrelated shell code")
    for value in old:
        text = text.replace('"' + value + '"', '"' + str(runtime) + '"')
    writes = {runtime: (SOURCE.read_bytes(), 0o644), launcher: (text.encode(), launcher.stat().st_mode & 0o777)}
    domains = mod.read_policy(root)
    owned(root / "profiles")
    profiles = [("default", root)] + [(p.name, p) for p in (root / "profiles").glob("*") if p.is_dir() and not p.is_symlink() and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", p.name)]
    for profile, home in profiles:
        owned(home)
        label = "ai.hermes.gateway" + ("-" + profile if profile != "default" else "")
        current = mod.loaded_domain(label)
        keyring = mod.keyring_enabled(home)
        target = f"gui/{os.getuid()}" if keyring else current or domains.get(label)
        if target:
            if keyring:
                mod.require_domain(target)
            domains[label] = target
        plist = Path.home() / "Library/LaunchAgents" / (label + ".plist")
        if plist.exists():
            owned(plist)
            writes[plist] = (mod.guard_plist(plist.read_text(), runtime, keyring).encode(), 0o644)
    policy = mod.policy_path(root)
    writes[policy] = ((json.dumps({"schema": 1, "domains": domains}, indent=2) + "\n").encode(), 0o600)
    changed = {p: pair for p, pair in writes.items() if not p.exists() or p.read_bytes() != pair[0]}
    if not changed:
        return {"status": "already-installed", "profiles": len(profiles)}
    backup = root / "plugin-backups" / ("macos-launchd-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f"))
    owned(backup.parent)
    backup.mkdir(parents=True, mode=0o700)
    metadata = []
    for index, path in enumerate(changed):
        owned(path)
        entry = {"path": str(path), "file": str(index), "existed": path.exists()}
        if path.exists():
            shutil.copy2(path, backup / str(index))
            entry["mode"] = path.stat().st_mode & 0o777
        metadata.append(entry)
    (backup / "metadata.json").write_text(json.dumps(metadata, indent=2))
    try:
        for path, (data, mode) in changed.items():
            atomic(path, data, mode)
    except Exception:
        restore(root, backup)
        raise
    return {"status": "installed", "profiles": len(profiles), "backup": str(backup)}


def restore(root, backup):
    owned(backup)
    if backup.parent.resolve() != (root / "plugin-backups").resolve():
        raise ValueError("backup must belong to this Hermes installation")
    for entry in reversed(json.loads((backup / "metadata.json").read_text())):
        path = Path(entry["path"]); owned(path)
        allowed = path.resolve().is_relative_to(root.resolve()) or path == Path.home() / ".local/bin/hermes" or path.parent == Path.home() / "Library/LaunchAgents" and path.name.startswith("ai.hermes.gateway")
        if not allowed or path.resolve().is_relative_to((root / "hermes-agent").resolve()) or not entry["file"].isdecimal():
            raise ValueError("backup target is outside the managed host paths")
        owned(backup / entry["file"])
        if entry["existed"]:
            atomic(path, (backup / entry["file"]).read_bytes(), entry["mode"])
        else:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(); p.add_argument("--home", type=Path, default=Path.home() / ".hermes")
    p.add_argument("--launcher", type=Path, default=Path.home() / ".local/bin/hermes")
    p.add_argument("--restore", type=Path); args = p.parse_args()
    if args.restore:
        restore(args.home, args.restore); print(json.dumps({"status": "restored"}))
    else:
        print(json.dumps(install(args.home, args.launcher)))
