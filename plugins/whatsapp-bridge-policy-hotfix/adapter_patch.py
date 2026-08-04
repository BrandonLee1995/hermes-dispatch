from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Final, Protocol

PATCH_ATTR: Final = "_whatsapp_bridge_policy_hotfix_applied"
MIXIN_PATCH_ATTR: Final = "_whatsapp_bridge_policy_hotfix_mixin_applied"
FACTORY_PATCH_ATTR: Final = "_whatsapp_bridge_policy_hotfix_factory_applied"


class AllowlistAdapter(Protocol):
    _allow_from: set[str]
    _group_allow_from: set[str]

    def _coerce_allow_list(self, raw: str) -> set[str]: ...

    def _matches_whatsapp_allowlist(
        self,
        candidate: str,
        allow_from: set[str] | None,
    ) -> bool: ...


def _env_list(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None


def apply_env_allowlists(adapter: AllowlistAdapter) -> None:
    dm_allowed = _env_list("WHATSAPP_ALLOWED_USERS")
    if dm_allowed is not None:
        setattr(adapter, "_allow_from", _coerce_allow_list(adapter, dm_allowed))
    group_allowed = _env_list("WHATSAPP_GROUP_ALLOWED_USERS")
    if group_allowed is not None:
        setattr(adapter, "_group_allow_from", _coerce_allow_list(adapter, group_allowed))


def apply_require_mention_compat(adapter: Any, config_path: Path | None = None) -> bool:
    """Honor Hermes 0.20 Dashboard's misplaced display-level setting.

    The WhatsApp adapter reads ``platforms.whatsapp.require_mention`` (bridged
    into ``config.extra``) or ``WHATSAPP_REQUIRE_MENTION``.  Hermes 0.20 can
    leave the Dashboard value under ``display.platforms.whatsapp`` instead,
    where it only affects presentation settings and the adapter silently falls
    back to ``false``.  Use that value only when the authoritative adapter/env
    setting is absent.
    """
    extra = getattr(getattr(adapter, "config", None), "extra", None)
    if not isinstance(extra, dict):
        return False
    if extra.get("require_mention") is not None or _env_list("WHATSAPP_REQUIRE_MENTION") is not None:
        return False

    if config_path is None:
        hermes_home = Path(os.getenv("HERMES_HOME", "~/.hermes")).expanduser()
        config_path = Path(os.getenv("HERMES_CONFIG", str(hermes_home / "config.yaml"))).expanduser()
    try:
        import yaml

        document = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        display = document.get("display") if isinstance(document, dict) else None
        platforms = display.get("platforms") if isinstance(display, dict) else None
        whatsapp = platforms.get("whatsapp") if isinstance(platforms, dict) else None
        value = whatsapp.get("require_mention") if isinstance(whatsapp, dict) else None
    except (OSError, ValueError, TypeError):
        return False
    if value is None:
        return False
    extra["require_mention"] = value
    return True


def _coerce_allow_list(adapter: AllowlistAdapter, raw: str) -> set[str]:
    return adapter._coerce_allow_list(raw)


def _env_backed_allowlist(
    adapter: AllowlistAdapter,
    attr: str,
    env_name: str,
) -> set[str] | None:
    current = getattr(adapter, attr, None)
    if current:
        return current
    raw = _env_list(env_name)
    if raw is None:
        return current
    parsed = _coerce_allow_list(adapter, raw)
    setattr(adapter, attr, parsed)
    return parsed


def patch_whatsapp_common_policy() -> None:
    from gateway.platforms.whatsapp_common import WhatsAppBehaviorMixin

    if getattr(WhatsAppBehaviorMixin, MIXIN_PATCH_ATTR, False):
        return

    original_is_dm_allowed: Callable = WhatsAppBehaviorMixin._is_dm_allowed
    original_is_dm_intake_allowed: Callable = WhatsAppBehaviorMixin._is_dm_intake_allowed
    original_is_group_allowed: Callable = WhatsAppBehaviorMixin._is_group_allowed

    def patched_is_dm_allowed(self, sender_id: str) -> bool:
        if getattr(self, "_dm_policy", None) == "allowlist":
            allow_from = _env_backed_allowlist(
                self, "_allow_from", "WHATSAPP_ALLOWED_USERS"
            )
            return self._matches_whatsapp_allowlist(sender_id, allow_from)
        return original_is_dm_allowed(self, sender_id)

    def patched_is_dm_intake_allowed(self, sender_id: str) -> bool:
        if getattr(self, "_dm_policy", None) == "allowlist":
            principal = str(sender_id or "").strip()
            if not principal:
                return False
            allow_from = _env_backed_allowlist(
                self, "_allow_from", "WHATSAPP_ALLOWED_USERS"
            )
            return self._matches_whatsapp_allowlist(principal, allow_from)
        return original_is_dm_intake_allowed(self, sender_id)

    def patched_is_group_allowed(self, chat_id: str) -> bool:
        if getattr(self, "_group_policy", None) == "allowlist":
            allow_from = _env_backed_allowlist(
                self, "_group_allow_from", "WHATSAPP_GROUP_ALLOWED_USERS"
            )
            return self._matches_whatsapp_allowlist(chat_id, allow_from)
        return original_is_group_allowed(self, chat_id)

    WhatsAppBehaviorMixin._is_dm_allowed = patched_is_dm_allowed
    WhatsAppBehaviorMixin._is_dm_intake_allowed = patched_is_dm_intake_allowed
    WhatsAppBehaviorMixin._is_group_allowed = patched_is_group_allowed
    setattr(WhatsAppBehaviorMixin, MIXIN_PATCH_ATTR, True)


def _patch_adapter_class(WhatsAppAdapter, runtime_bridge: Path | None) -> bool:
    if getattr(WhatsAppAdapter, PATCH_ATTR, False):
        return False

    original_init: Callable = WhatsAppAdapter.__init__

    def patched_init(self, config) -> None:
        original_init(self, config)
        apply_env_allowlists(self)
        apply_require_mention_compat(self)
        if runtime_bridge is not None:
            self._bridge_script = str(runtime_bridge)

    WhatsAppAdapter.__init__ = patched_init
    setattr(WhatsAppAdapter, PATCH_ATTR, True)
    return True


def _patch_registry_entry(entry, runtime_bridge: Path | None) -> bool:
    original_factory: Callable = entry.adapter_factory
    if getattr(original_factory, FACTORY_PATCH_ATTR, False):
        return False

    def patched_factory(config):
        adapter = original_factory(config)
        apply_env_allowlists(adapter)
        apply_require_mention_compat(adapter)
        if runtime_bridge is not None:
            adapter._bridge_script = str(runtime_bridge)
        return adapter

    setattr(patched_factory, FACTORY_PATCH_ATTR, True)
    entry.adapter_factory = patched_factory
    return True


def patch_whatsapp_adapter(runtime_bridge: Path | None) -> None:
    patch_whatsapp_common_policy()

    patched_any = False
    import_errors = []

    # Hermes 0.20 registers bundled platforms lazily. Resolve the live entry
    # before patching so its real module is loaded, then wrap the factory as a
    # stable seam even if upstream changes module aliases again.
    try:
        from gateway.platform_registry import platform_registry

        entry = platform_registry.get("whatsapp")
        if entry is not None:
            patched_any = _patch_registry_entry(entry, runtime_bridge) or patched_any
    except ImportError as exc:
        import_errors.append(f"gateway.platform_registry: {exc}")

    for module_name in (
        "hermes_plugins.whatsapp_platform.adapter",
        "plugins.platforms.whatsapp.adapter",
    ):
        try:
            module = __import__(module_name, fromlist=["WhatsAppAdapter"])
        except ImportError as exc:
            import_errors.append(f"{module_name}: {exc}")
            continue
        WhatsAppAdapter = getattr(module, "WhatsAppAdapter", None)
        if WhatsAppAdapter is not None:
            patched_any = _patch_adapter_class(WhatsAppAdapter, runtime_bridge) or patched_any

    if not patched_any and import_errors:
        return
