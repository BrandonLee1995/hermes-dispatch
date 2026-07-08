"""Verify the Hermes MCP QQBot target parser compatibility patch.

How to run:
    python scripts/test_hermes_mcp_qqbot_target_patch.py
"""

from __future__ import annotations

from types import ModuleType

from hermes_mcp_qqbot_target_patch import _qqbot_media_file_type, install_qqbot_target_patch


def main() -> None:
    module = ModuleType("fake_send_message_tool")

    def original(platform_name: str, target_ref: str) -> tuple[str | None, str | None, bool]:
        return None, None, False

    async def original_send_to_platform(*args, **kwargs):
        return {"success": True}

    module._parse_target_ref = original
    module._send_to_platform = original_send_to_platform

    install_qqbot_target_patch(module)
    parsed = module._parse_target_ref("qqbot", "B279C1A461933B21DAFEE3263B8854A6")
    assert parsed == ("B279C1A461933B21DAFEE3263B8854A6", None, True), parsed

    typed = module._parse_target_ref("qqbot", "group:B279C1A461933B21DAFEE3263B8854A6")
    assert typed == ("B279C1A461933B21DAFEE3263B8854A6", None, True), typed

    other_platform = module._parse_target_ref("telegram", "B279C1A461933B21DAFEE3263B8854A6")
    assert other_platform == (None, None, False), other_platform

    try:
        from gateway.platforms.qqbot.constants import MEDIA_TYPE_FILE, MEDIA_TYPE_IMAGE
    except ModuleNotFoundError:
        media_type_checked = False
    else:
        assert _qqbot_media_file_type("/tmp/a.png") == MEDIA_TYPE_IMAGE
        assert _qqbot_media_file_type("/tmp/a.txt") == MEDIA_TYPE_FILE
        media_type_checked = True

    print("qqbot_raw_openid_explicit=true")
    print("qqbot_typed_openid_explicit=true")
    print("other_platform_unchanged=true")
    print(f"qqbot_media_file_type={str(media_type_checked).lower()}")


if __name__ == "__main__":
    main()
