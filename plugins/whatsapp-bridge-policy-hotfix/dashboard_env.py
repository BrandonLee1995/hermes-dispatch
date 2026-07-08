from __future__ import annotations

from typing import Final

EnvMeta = dict[str, str | bool | None]

ENV_VARS: Final[dict[str, EnvMeta]] = {
    "WHATSAPP_DM_POLICY": {
        "description": "WhatsApp DM policy. Use allowlist to keep private chats limited to WHATSAPP_ALLOWED_USERS",
        "prompt": "WhatsApp DM policy",
        "url": None,
        "password": False,
        "category": "messaging",
    },
    "WHATSAPP_GROUP_POLICY": {
        "description": "WhatsApp group policy. Use open for all joined groups; Hermes also requires WHATSAPP_ALLOW_ALL_USERS=true as an explicit open opt-in",
        "prompt": "WhatsApp group policy",
        "url": None,
        "password": False,
        "category": "messaging",
    },
    "WHATSAPP_GROUP_ALLOWED_USERS": {
        "description": "Comma-separated WhatsApp group JIDs allowed when group policy is allowlist",
        "prompt": "Allowed WhatsApp group JIDs",
        "url": None,
        "password": False,
        "category": "messaging",
    },
    "WHATSAPP_REQUIRE_MENTION": {
        "description": "When true, WhatsApp group messages only trigger the agent when they mention the bot, reply to the bot, use a slash command, or match WHATSAPP_MENTION_PATTERNS",
        "prompt": "WhatsApp require mention",
        "url": None,
        "password": False,
        "category": "messaging",
    },
    "WHATSAPP_ALLOW_ADMIN_FROM": {
        "description": "Comma-separated WhatsApp user IDs allowed to run all admin slash commands in DMs",
        "prompt": "WhatsApp DM admins",
        "url": None,
        "password": False,
        "category": "messaging",
    },
    "WHATSAPP_USER_ALLOWED_COMMANDS": {
        "description": "Comma-separated slash commands non-admin WhatsApp DM users may run",
        "prompt": "WhatsApp DM user commands",
        "url": None,
        "password": False,
        "category": "messaging",
    },
    "WHATSAPP_GROUP_ALLOW_ADMIN_FROM": {
        "description": "Comma-separated WhatsApp user IDs allowed to run all admin slash commands in groups",
        "prompt": "WhatsApp group admins",
        "url": None,
        "password": False,
        "category": "messaging",
    },
    "WHATSAPP_GROUP_USER_ALLOWED_COMMANDS": {
        "description": "Comma-separated slash commands non-admin WhatsApp group users may run",
        "prompt": "WhatsApp group user commands",
        "url": None,
        "password": False,
        "category": "messaging",
    },
}


def patch_dashboard_env_metadata() -> None:
    from hermes_cli import config as hermes_config

    for name, meta in ENV_VARS.items():
        hermes_config.OPTIONAL_ENV_VARS.setdefault(name, meta)
