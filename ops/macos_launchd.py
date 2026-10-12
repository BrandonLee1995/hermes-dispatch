"""Removable host CLI guard for profile-scoped launchd domains (#21)."""
from __future__ import annotations

import functools
import json
import os
from pathlib import Path
import plistlib
import re
import runpy
import socket
import subprocess
import sys
import tempfile
import time
import tomllib

DOMAIN_ENV = "HERMES_DISPATCH_LAUNCHD_DOMAIN"
MARKER = "_dispatch_macos_launchd_guard"


def checked_domain(domain: str) -> str:
    if domain not in (f"gui/{os.getuid()}", f"user/{os.getuid()}"):
        raise ValueError("launchd domain must belong to the invoking user")
    return domain


def loaded_domain(label: str) -> str | None:
    found = []
    for kind in ("gui", "user"):
        domain = f"{kind}/{os.getuid()}"
        p = subprocess.run(["launchctl", "print", f"{domain}/{label}"],
                           capture_output=True, timeout=10)
        if p.returncode == 0:
            found.append(domain)
    if len(found) > 1:
        raise RuntimeError("Gateway is registered in both launchd domains; refusing an ambiguous update")
    return found[0] if found else None


def policy_path(root: Path) -> Path:
    return root / "state" / "dispatch-launchd-domains.json"


def read_policy(root: Path) -> dict[str, str]:
    path = policy_path(root)
    if not path.exists():
        return {}
    if path.is_symlink() or path.stat().st_uid != os.getuid():
        raise ValueError("launchd policy must be a regular owner-owned file")
    data = json.loads(path.read_text())
    domains = data["domains"]
    if data.get("schema") != 1 or not isinstance(domains, dict):
        raise ValueError("invalid launchd policy")
    for label, domain in domains.items():
        if not re.fullmatch(r"ai\.hermes\.gateway(?:-[a-z0-9][a-z0-9_-]{0,63})?", label):
            raise ValueError("invalid Gateway service label")
        checked_domain(domain)
    return domains


def remember_domain(root: Path, label: str, domain: str) -> None:
    domains = read_policy(root)
    domains[label] = checked_domain(domain)
    path = policy_path(root)
    if path.parent.is_symlink():
        raise ValueError("launchd policy directory must not be a symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".launchd-domains-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump({"schema": 1, "domains": domains}, handle, indent=2)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def keyring_enabled(home: Path) -> bool:
    import yaml
    from dotenv import dotenv_values
    config_path = home / "config.yaml"
    if not config_path.is_file():
        return False
    cfg = yaml.safe_load(config_path.read_text()) or {}
    model = cfg.get("model", {})
    if model.get("provider") not in ("openai", "openai-codex") or model.get("openai_runtime") != "codex_app_server":
        return False
    env = dotenv_values(home / ".env")
    codex_home = Path(env.get("CODEX_HOME") or os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()
    config = codex_home / "config.toml"
    if not config.is_file():
        return False
    # Only explicit keyring profiles change strategy; other profiles retain their domain.
    with config.open("rb") as handle:
        return tomllib.load(handle).get("cli_auth_credentials_store") == "keyring"


def require_domain(domain: str) -> None:
    checked_domain(domain)
    p = subprocess.run(["launchctl", "print", domain], capture_output=True, timeout=10)
    if p.returncode:
        raise RuntimeError("Required launchd domain is unavailable; Codex keyring needs a logged-in GUI session")


def guard_plist(text: str, script: Path, keyring: bool) -> str:
    data = plistlib.loads(text.encode())
    args = data["ProgramArguments"]
    for index in range(len(args) - 1):
        if args[index:index + 2] == ["-m", "hermes_cli.main"]:
            args[index:index + 2] = [str(script)]
            break
    else:
        old_script = str(script.parent / "hermes-keyring-launchd.py")
        if old_script in args:
            args[args.index(old_script)] = str(script)
        elif str(script) not in args:
            raise RuntimeError("Unrecognized Hermes service entrypoint; refusing an unguarded refresh")
    if keyring:
        data["LimitLoadToSessionType"] = ["Aqua"]
    data.get("EnvironmentVariables", {}).pop(DOMAIN_ENV, None)
    return plistlib.dumps(data, sort_keys=False).decode()


def patch_gateway(gateway, root: Path, script: Path) -> None:
    if getattr(gateway, MARKER, False):
        return
    original_probe = gateway._probe_launchd_domain_for_label
    original_generate = gateway.generate_launchd_plist
    original_bootstrap = gateway._launchctl_bootstrap
    original_fallback = gateway._launchd_fallback_to_detached
    original_stop = gateway.launchd_stop
    original_restart = gateway.launchd_restart
    original_run = gateway.run_gateway

    def home():
        return Path(gateway.get_hermes_home())

    def managed():
        return home().resolve() == root.resolve() or home().resolve().parent == (root / "profiles").resolve()

    def target():
        label = gateway.get_launchd_label()
        if keyring_enabled(home()):
            return f"gui/{os.getuid()}"
        return read_policy(root).get(label) or loaded_domain(label) or original_probe(label)

    @functools.wraps(original_probe)
    def probe(label):
        if managed() and label == gateway.get_launchd_label():
            forced = os.environ.get(DOMAIN_ENV)
            if forced:
                return checked_domain(forced)
            return target()
        return read_policy(root).get(label) or original_probe(label)

    @functools.wraps(original_generate)
    def generate():
        text = original_generate()
        return guard_plist(text, script, keyring_enabled(home())) if managed() else text

    @functools.wraps(original_bootstrap)
    def bootstrap(domain, plist_path, label, **kwargs):
        if managed() and label == gateway.get_launchd_label():
            expected = target()
            if domain != expected:
                raise RuntimeError("Refusing to bootstrap Gateway outside its preserved launchd domain")
            require_domain(expected)
        return original_bootstrap(domain, plist_path, label, **kwargs)

    @functools.wraps(original_fallback)
    def fallback(reason, **kwargs):
        if managed() and (keyring_enabled(home()) or gateway.get_launchd_label() in read_policy(root)):
            raise RuntimeError("Preserved launchd service could not start; refusing detached Background fallback")
        return original_fallback(reason, **kwargs)

    @functools.wraps(original_stop)
    def stop():
        if not managed():
            return original_stop()
        label = gateway.get_launchd_label()
        current = loaded_domain(label)
        if current:
            remember_domain(root, label, f"gui/{os.getuid()}" if keyring_enabled(home()) else current)
            gateway._resolved_launchd_domain = current
        return original_stop()

    @functools.wraps(original_restart)
    def restart():
        label = gateway.get_launchd_label()
        if not managed() or not keyring_enabled(home()) and label not in read_policy(root):
            return original_restart()
        require_domain(target())
        from gateway.status import get_running_pid
        from gateway.config import load_gateway_config, Platform
        old_pid = get_running_pid()
        old_domain = loaded_domain(label)
        config = load_gateway_config().platforms.get(Platform("api_server"))
        endpoint = None
        if config and config.enabled:
            extra = config.extra or {}
            endpoint = (extra.get("host", os.getenv("API_SERVER_HOST", "127.0.0.1")),
                        int(extra.get("port") or os.getenv("API_SERVER_PORT", "8642")))
        stop()
        deadline = time.monotonic() + 90
        while True:
            alive = False
            if old_pid:
                try:
                    os.kill(old_pid, 0)
                    alive = True
                except ProcessLookupError:
                    pass
            port_free = True
            if endpoint:
                with socket.socket(socket.AF_INET6 if ":" in endpoint[0] else socket.AF_INET, socket.SOCK_STREAM) as sock:
                    try:
                        sock.bind(endpoint)
                    except OSError:
                        port_free = False
            unloaded = not old_domain or subprocess.run(["launchctl", "print", f"{old_domain}/{label}"],
                                                        capture_output=True, timeout=10).returncode != 0
            if not alive and port_free and unloaded:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("Old Gateway/PID/API port/launchd registration did not drain; refusing overlapping restart")
            time.sleep(.5)
        gateway._resolved_launchd_domain = target()
        gateway.launchd_start()
        if not gateway._wait_for_launchd_service_pid(label, old_pid, timeout=30, domain=target()):
            raise RuntimeError("Gateway did not register a replacement PID in its preserved domain")

    @functools.wraps(original_run)
    def run(*args, **kwargs):
        if managed() and keyring_enabled(home()):
            manager = subprocess.run(["launchctl", "managername"], capture_output=True, text=True, timeout=10)
            if manager.stdout.strip() != "Aqua":
                raise RuntimeError("Codex keyring requires the macOS Aqua session; refusing Background gateway run")
        return original_run(*args, **kwargs)

    gateway._probe_launchd_domain_for_label = probe
    gateway.generate_launchd_plist = generate
    gateway._launchctl_bootstrap = bootstrap
    gateway._launchd_fallback_to_detached = fallback
    gateway.launchd_stop = stop
    gateway.launchd_restart = restart
    gateway.run_gateway = run
    gateway._resolved_launchd_domain = None
    setattr(gateway, MARKER, True)


def main():
    root = Path.home() / ".hermes"
    core = root / "hermes-agent"
    sys.path.insert(0, str(core))
    from hermes_cli import gateway
    patch_gateway(gateway, root, Path(__file__).resolve())
    sys.argv[0] = str(core / "hermes")
    runpy.run_path(str(core / "hermes"), run_name="__main__")


if __name__ == "__main__":
    main()
