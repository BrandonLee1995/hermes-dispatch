"""Only the existing Hermes MCP is made required; config files stay untouched."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mcp_startup import wrap_client_init


class StartupTests(unittest.TestCase):
    def test_existing_server_keeps_args_env_and_file_bytes(self):
        with tempfile.TemporaryDirectory() as home:
            config = Path(home, 'config.toml')
            content = '[mcp_servers.hermes-tools]\ncommand="python"\nstartup_timeout_sec=25\n'
            config.write_text(content)
            observed = []
            wrapped = wrap_client_init(lambda self, **kw: observed.append(kw))
            args = ['-c', 'model="fixture"']
            env = {'CODEX_HOME': home, 'FIXTURE_SETTING': 'keep'}
            wrapped(object(), 'fixture-codex', None, args, env)
            self.assertEqual(observed[0]['extra_args'], args + ['-c', 'mcp_servers.hermes-tools.required=true'])
            self.assertEqual(args, ['-c', 'model="fixture"'])
            self.assertIs(observed[0]['env'], env)
            self.assertEqual(config.read_text(), content)

    def test_missing_disabled_and_already_required_servers_are_unchanged(self):
        cases = ['', '[mcp_servers.other]\ncommand="python"\n',
                 '[mcp_servers.hermes-tools]\ncommand="python"\nenabled=false\n',
                 '[mcp_servers.hermes-tools]\ncommand="python"\nrequired=true\n']
        with tempfile.TemporaryDirectory() as home:
            for text in cases:
                with self.subTest(text=text):
                    Path(home, 'config.toml').write_text(text)
                    wrapped = wrap_client_init(lambda self, **kw: kw)
                    result = wrapped(object(), codex_home=home)
                    self.assertIsNone(result['extra_args'])
            Path(home, 'config.toml').unlink()
            self.assertIsNone(wrapped(object(), codex_home=home)['extra_args'])

    def test_explicit_home_wins_and_environment_home_is_supported(self):
        with tempfile.TemporaryDirectory() as present, tempfile.TemporaryDirectory() as absent:
            Path(present, 'config.toml').write_text('[mcp_servers.hermes-tools]\ncommand="python"\n')
            wrapped = wrap_client_init(lambda self, **kw: kw)
            with patch.dict(os.environ, {'CODEX_HOME': present}):
                self.assertIn('mcp_servers.hermes-tools.required=true', wrapped(object())['extra_args'])
                self.assertIsNone(wrapped(object(), codex_home=absent, env={'CODEX_HOME': present})['extra_args'])


if __name__ == '__main__':
    unittest.main()
