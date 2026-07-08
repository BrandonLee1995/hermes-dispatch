from __future__ import annotations

import os
from typing import Final, Protocol

PATCH_ATTR: Final = "_whatsapp_bridge_policy_hotfix_applied"


class PlatformValue(Protocol):
    value: str


class SlashSource(Protocol):
    platform: str | PlatformValue
    chat_type: str | None


def _platform_value(source: SlashSource) -> str:
    platform = getattr(source, "platform", "")
    return str(getattr(platform, "value", platform))


def _scope_value(source: SlashSource) -> str:
    chat_type = str(getattr(source, "chat_type", "") or "").lower()
    if chat_type in {"dm", "direct", "private", ""}:
        return "dm"
    return "group"


def _env_value(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return None


def env_extra_for_source(source: SlashSource) -> dict[str, str]:
    if _platform_value(source) != "whatsapp":
        return {}
    if _scope_value(source) == "group":
        extra: dict[str, str] = {}
        admins = _env_value(
            "WHATSAPP_GROUP_ALLOW_ADMIN_FROM",
            "WHATSAPP_GROUP_ADMIN_ALLOWED_USERS",
        )
        commands = _env_value("WHATSAPP_GROUP_USER_ALLOWED_COMMANDS")
        if admins is not None:
            extra["group_allow_admin_from"] = admins
        if commands is not None:
            extra["group_user_allowed_commands"] = commands
        return extra

    extra = {}
    admins = _env_value("WHATSAPP_ALLOW_ADMIN_FROM", "WHATSAPP_ADMIN_ALLOWED_USERS")
    commands = _env_value("WHATSAPP_USER_ALLOWED_COMMANDS")
    if admins is not None:
        extra["allow_admin_from"] = admins
    if commands is not None:
        extra["user_allowed_commands"] = commands
    return extra


def patch_slash_access_env_policy() -> None:
    from gateway import slash_access

    if getattr(slash_access, PATCH_ATTR, False):
        return

    original_policy_for_source = slash_access.policy_for_source

    def patched_policy_for_source(gateway_config, source):
        env_extra = env_extra_for_source(source)
        if env_extra:
            scope = _scope_value(source)
            env_policy = slash_access.policy_from_extra(env_extra, scope)
            if env_policy.enabled:
                return env_policy
        return original_policy_for_source(gateway_config, source)

    slash_access.policy_for_source = patched_policy_for_source
    setattr(slash_access, PATCH_ATTR, True)
