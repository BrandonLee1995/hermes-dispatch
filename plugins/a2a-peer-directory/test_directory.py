"""Directory metadata preserves native output and never includes credentials."""
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("peer_directory", Path(__file__).with_name("__init__.py"))
directory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(directory)


class DirectoryTests(unittest.TestCase):
    def setUp(self):
        self.config = {"a2a_agents": {}}
        self.base = "Configured peers\n\nPersisted conversations\n  - ctx-fixture\n\nMetrics: 1 in / 2 out"
        self.tools = SimpleNamespace(
            a2a_list=lambda *args, **kwargs: self.base,
            _load_config=lambda: self.config,
            _a2a_tools_available=lambda: True,
            _SCHEMAS={"a2a_list": {"function": {"name": "a2a_list", "description": "native", "parameters": {}}}},
        )
        module = ModuleType("plugins.platforms.a2a")
        module.tools = self.tools
        self.patch = patch.dict(sys.modules, {"plugins.platforms.a2a": module})
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_descriptions_preserve_history_metrics_and_exclude_auth(self):
        self.config["a2a_agents"] = {
            "product-support": {"description": "产品支持\n  型号与故障排查", "auth": {"token": "never-display"}},
            "product": {"description": "产品需求与开发协作"},
        }
        result = directory.a2a_list({})
        self.assertTrue(result.startswith(self.base + "\n\n"))
        self.assertIn("  - product-support: 产品支持 型号与故障排查", result)
        self.assertIn("  - product: 产品需求与开发协作", result)
        self.assertNotIn("never-display", result)
        self.config = {"a2a_agents": {"procurement": {"description": "询价与交期"}}}
        self.assertNotIn("product-support", directory.a2a_list())

    def test_absent_blank_or_invalid_descriptions_keep_original_result(self):
        for description in (None, "", "  ", 123, {"token": "never-display"}):
            self.config["a2a_agents"] = {"peer": {"description": description}}
            self.assertEqual(directory.a2a_list(), self.base)
        self.config = {}
        self.assertEqual(directory.a2a_list(), self.base)

    def test_public_registration_preserves_gate_and_native_schema(self):
        calls = []
        directory.register(SimpleNamespace(register_tool=lambda **kwargs: calls.append(kwargs)))
        self.assertEqual(len(calls), 1)
        self.assertIs(calls[0]["handler"], directory.a2a_list)
        self.assertIs(calls[0]["check_fn"], self.tools._a2a_tools_available)
        self.assertTrue(calls[0]["override"])
        self.assertIn("responsibility descriptions", calls[0]["schema"]["description"])
        self.assertEqual(self.tools._SCHEMAS["a2a_list"]["function"]["description"], "native")


if __name__ == "__main__":
    unittest.main()
