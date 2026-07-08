import importlib.util
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace


def load_plugin_module():
    path = Path("/opt/data/plugins/whatsapp-bridge-policy-hotfix/__init__.py")
    if not path.exists():
        path = Path(__file__).with_name("__init__.py")
    spec = importlib.util.spec_from_file_location(
        "whatsapp_bridge_policy_hotfix_test",
        path,
        submodule_search_locations=[str(path.parent)],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "whatsapp_bridge_policy_hotfix_test"
    mod.__path__ = [str(path.parent)]
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


mod = load_plugin_module()


def test_bridge_patch_bypasses_dm_allowlist_for_groups():
    from whatsapp_bridge_policy_hotfix_test.bridge_runtime import (
        PATCH_MARKER,
        patched_bridge_source,
    )

    source = (
        "if (!msg.key.fromMe) {\n"
        "        if (!matchesAllowedUser(senderId, ALLOWED_USERS, SESSION_DIR)) {\n"
        "          continue;\n"
        "        }\n"
        "}\n"
    )

    patched = patched_bridge_source(source)

    assert PATCH_MARKER in patched
    assert "if (!isGroup && !matchesAllowedUser(senderId, ALLOWED_USERS, SESSION_DIR)) {" in patched
    assert patched_bridge_source(patched) == patched


def test_adapter_env_allowlists_are_scope_separated():
    from whatsapp_bridge_policy_hotfix_test.adapter_patch import (
        _env_backed_allowlist,
        apply_env_allowlists,
    )

    class Adapter:
        def __init__(self):
            self._allow_from = set()
            self._group_allow_from = set()

        @staticmethod
        def _coerce_allow_list(raw):
            return {part.strip() for part in str(raw).split(",") if part.strip()}

    old_dm = os.environ.get("WHATSAPP_ALLOWED_USERS")
    old_group = os.environ.get("WHATSAPP_GROUP_ALLOWED_USERS")
    os.environ["WHATSAPP_ALLOWED_USERS"] = "15551234567"
    os.environ["WHATSAPP_GROUP_ALLOWED_USERS"] = "120363000000000000@g.us"
    try:
        adapter = Adapter()
        apply_env_allowlists(adapter)
        assert adapter._allow_from == {"15551234567"}
        assert adapter._group_allow_from == {"120363000000000000@g.us"}

        adapter._allow_from = set()
        assert _env_backed_allowlist(
            adapter, "_allow_from", "WHATSAPP_ALLOWED_USERS"
        ) == {"15551234567"}
    finally:
        if old_dm is None:
            os.environ.pop("WHATSAPP_ALLOWED_USERS", None)
        else:
            os.environ["WHATSAPP_ALLOWED_USERS"] = old_dm
        if old_group is None:
            os.environ.pop("WHATSAPP_GROUP_ALLOWED_USERS", None)
        else:
            os.environ["WHATSAPP_GROUP_ALLOWED_USERS"] = old_group


def test_runtime_install_patches_data_bridge_path():
    from whatsapp_bridge_policy_hotfix_test.bridge_runtime import (
        PATCH_MARKER,
        install_runtime_bridge,
    )

    source = (
        "if (!msg.key.fromMe) {\n"
        "        if (!matchesAllowedUser(senderId, ALLOWED_USERS, SESSION_DIR)) {\n"
        "          continue;\n"
        "        }\n"
        "}\n"
    )

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        source_dir = root / "source"
        runtime_dir = root / "runtime"
        data_dir = root / "data"
        source_dir.mkdir()
        data_dir.mkdir()
        (source_dir / "bridge.js").write_text(source, encoding="utf-8")
        (data_dir / "bridge.js").write_text(source, encoding="utf-8")

        runtime_bridge = install_runtime_bridge(source_dir, runtime_dir, data_dir)

        assert PATCH_MARKER in runtime_bridge.read_text(encoding="utf-8")
        assert PATCH_MARKER in (data_dir / "bridge.js").read_text(encoding="utf-8")


def test_slash_admin_envs_are_scoped():
    from whatsapp_bridge_policy_hotfix_test.slash_admin import env_extra_for_source

    old_values = {
        key: os.environ.get(key)
        for key in (
            "WHATSAPP_ALLOW_ADMIN_FROM",
            "WHATSAPP_USER_ALLOWED_COMMANDS",
            "WHATSAPP_GROUP_ALLOW_ADMIN_FROM",
            "WHATSAPP_GROUP_USER_ALLOWED_COMMANDS",
        )
    }
    os.environ["WHATSAPP_ALLOW_ADMIN_FROM"] = "dm-admin"
    os.environ["WHATSAPP_USER_ALLOWED_COMMANDS"] = "help,status"
    os.environ["WHATSAPP_GROUP_ALLOW_ADMIN_FROM"] = "group-admin"
    os.environ["WHATSAPP_GROUP_USER_ALLOWED_COMMANDS"] = "help,whoami"
    try:
        dm_source = SimpleNamespace(platform=SimpleNamespace(value="whatsapp"), chat_type="dm")
        group_source = SimpleNamespace(platform=SimpleNamespace(value="whatsapp"), chat_type="group")
        assert env_extra_for_source(dm_source) == {
            "allow_admin_from": "dm-admin",
            "user_allowed_commands": "help,status",
        }
        assert env_extra_for_source(group_source) == {
            "group_allow_admin_from": "group-admin",
            "group_user_allowed_commands": "help,whoami",
        }
    finally:
        for key, value in old_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_dashboard_env_metadata_includes_require_mention():
    from whatsapp_bridge_policy_hotfix_test.dashboard_env import ENV_VARS

    meta = ENV_VARS["WHATSAPP_REQUIRE_MENTION"]

    assert meta["prompt"] == "WhatsApp require mention"
    assert "group" in str(meta["description"]).lower()
    assert "mention" in str(meta["description"]).lower()
    assert meta["password"] is False


if __name__ == "__main__":
    test_bridge_patch_bypasses_dm_allowlist_for_groups()
    test_adapter_env_allowlists_are_scope_separated()
    test_runtime_install_patches_data_bridge_path()
    test_slash_admin_envs_are_scoped()
    test_dashboard_env_metadata_includes_require_mention()
    print("bridge_group_dm_allowlist_bypass=ok")
    print("adapter_env_scope_split=ok")
    print("runtime_data_bridge_patch=ok")
    print("slash_admin_env_scope=ok")
    print("dashboard_require_mention_metadata=ok")
