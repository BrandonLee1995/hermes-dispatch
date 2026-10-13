"""Exercise the opt-in CLI against a real local MCP HTTP boundary."""
import http.server
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import yaml

SCRIPT = Path(__file__).with_name('enable_weknora_routing.py')

class Server(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self): self.send_error(405)
    def do_POST(self):
        q = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.calls.append(q.get('method'))
        if 'id' not in q:
            self.send_response(202); self.end_headers(); return
        method = q['method']
        if method == 'initialize':
            result = {'protocolVersion': '2025-06-18', 'capabilities': {'tools': {}}, 'serverInfo': {'name': 'fixture', 'version': '1'}}
        elif method == 'tools/list':
            result = {'tools': [{'name': n, 'inputSchema': {'type': 'object'}} for n in ['hybrid_search', 'list_knowledge_bases', 'list_shared_knowledge_bases']]}
        else:
            result = {'content': [{'type': 'text', 'text': json.dumps({'success': not self.server.fail, 'data': []})}], 'isError': self.server.fail}
        data = json.dumps({'jsonrpc': '2.0', 'id': q['id'], 'result': result}).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)

class OptIn(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name); self.codex = self.home/'codex'; self.codex.mkdir()
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Server)
        self.server.calls = []; self.server.fail = False
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close); self.addCleanup(self.server.shutdown)
        self.url = f'http://127.0.0.1:{self.server.server_port}/mcp'
        (self.home/'.env').write_text('CODEX_HOME='+str(self.codex)+'\n')
        (self.codex/'config.toml').write_text('[mcp_servers.weknora]\nurl='+json.dumps(self.url)+'\nenabled=true\n')
        self.config = self.home/'config.yaml'
        self.config.write_text(yaml.safe_dump({'plugins': {'enabled': ['existing']}, 'model': {'openai_runtime': 'codex_app_server'}}))
    def run_cli(self, *args):
        p = subprocess.run([sys.executable, str(SCRIPT), '--home', str(self.home), *args], capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)
    def test_accessible_codex_connection_enables_only_profile_and_backs_up(self):
        before = self.config.read_bytes(); codex_before = (self.codex/'config.toml').read_bytes()
        self.assertEqual(self.run_cli()['status'], 'eligible')
        self.assertEqual(self.config.read_bytes(), before)
        result = self.run_cli('--apply')
        self.assertEqual(result['status'], 'enabled')
        cfg = yaml.safe_load(self.config.read_text())
        self.assertEqual(cfg['plugins']['enabled'], ['existing', 'weknora-qq-routing'])
        self.assertEqual(cfg['plugins']['entries']['weknora-qq-routing']['settings']['mode'], 'hermes')
        self.assertEqual(cfg['mcp_servers']['weknora']['url'], self.url)
        self.assertEqual(Path(result['backup']).read_bytes(), before)
        self.assertEqual((self.codex/'config.toml').read_bytes(), codex_before)
        self.assertEqual(self.server.calls.count('tools/call'), 4)

    def test_failed_inventory_does_not_enable(self):
        self.server.fail = True
        before = self.config.read_bytes()
        self.assertEqual(self.run_cli('--apply')['status'], 'skipped')
        self.assertEqual(self.config.read_bytes(), before)

    def test_no_connection_or_disabled_selection_does_not_probe(self):
        for config in [{'plugins': {'enabled': [], 'disabled': ['weknora-qq-routing']}},
                       {'plugins': {'enabled': [], 'entries': {'weknora-qq-routing': {'settings': {'mode': 'off'}}}}}]:
            self.config.write_text(yaml.safe_dump(config)); before = self.config.read_bytes()
            self.assertEqual(self.run_cli('--apply')['status'], 'skipped')
            self.assertEqual(self.config.read_bytes(), before)
        (self.codex/'config.toml').write_text('')
        self.config.write_text('plugins: {enabled: []}')
        self.assertEqual(self.run_cli('--apply')['status'], 'skipped')
        self.assertEqual(self.server.calls, [])

    def test_named_profile_cannot_borrow_default_codex_home(self):
        named = self.home/'profiles'/'department'; named.mkdir(parents=True)
        (named/'config.yaml').write_bytes(self.config.read_bytes())
        p = subprocess.run([sys.executable, str(SCRIPT), '--home', str(named), '--apply'],
                           env={**os.environ, 'CODEX_HOME': str(self.codex)}, capture_output=True, text=True)
        self.assertEqual(json.loads(p.stdout)['reason'], 'no-profile-codex-home')
        self.assertEqual(self.server.calls, [])

    def test_active_turn_defers_without_config_write(self):
        (self.home/'gateway_state.json').write_text('{"active_agents": 1}')
        before = self.config.read_bytes()
        self.assertEqual(self.run_cli('--apply')['status'], 'deferred')
        self.assertEqual(self.config.read_bytes(), before)

    def test_missing_search_and_mismatched_profile_are_not_enabled(self):
        with (self.codex/'config.toml').open('a') as f: f.write('disabled_tools=["hybrid_search"]\n')
        before = self.config.read_bytes()
        self.assertEqual(self.run_cli('--apply')['status'], 'skipped')
        self.assertEqual(self.config.read_bytes(), before)
        self.config.write_text(yaml.safe_dump({'plugins': {'enabled': []}, 'mcp_servers': {'weknora': {'url': self.url+'/different'}}}))
        self.server.calls.clear(); before = self.config.read_bytes()
        self.assertEqual(self.run_cli('--apply')['status'], 'skipped')
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual(self.server.calls, [])

if __name__ == '__main__': unittest.main()
