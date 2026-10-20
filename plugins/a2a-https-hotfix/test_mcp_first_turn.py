"""Real Codex + delayed MCP + local Responses fixture; no live model or credentials.

Run with CODEX_BINARY and PYTHONPATH pointing to an installed Hermes source.
"""
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from agent.transports.codex_app_server import CodexAppServerClient, CodexAppServerError
from mcp_startup import wrap_client_init


MCP_FIXTURE = '''import json, sys, time
time.sleep(2)
for line in sys.stdin:
    req = json.loads(line)
    if 'id' not in req:
        continue
    method = req['method']
    if method == 'initialize':
        result = {'protocolVersion': req['params']['protocolVersion'],
                  'capabilities': {'tools': {}}, 'serverInfo': {'name': 'fixture', 'version': '1'}}
    elif method == 'tools/list':
        result = {'tools': [{'name': 'a2a_call', 'description': 'Fixture only',
                  'inputSchema': {'type': 'object', 'properties': {}}}]}
    else:
        result = {}
    print(json.dumps({'jsonrpc': '2.0', 'id': req['id'], 'result': result}), flush=True)
'''


class Responses(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        outputs = [x.get('output') for x in data.get('input', [])
                   if x.get('type') in {'custom_tool_call_output', 'function_call_output'}]
        self.server.outputs.extend(outputs)
        self.server.requests += 1
        if self.server.requests == 1:
            item = {'type': 'custom_tool_call', 'id': 'ctc_fixture', 'call_id': 'call_fixture',
                    'status': 'completed', 'name': 'exec',
                    'input': 'text(JSON.stringify({a2a_names: ALL_TOOLS.filter(x => x.name.includes("a2a_call")).map(x => x.name)}));'}
        else:
            item = {'type': 'message', 'id': 'msg_fixture', 'role': 'assistant',
                    'status': 'completed', 'phase': 'final_answer',
                    'content': [{'type': 'output_text', 'text': 'READY', 'annotations': []}]}
        events = [
            {'type': 'response.created', 'response': {'id': 'resp_fixture', 'object': 'response',
             'status': 'in_progress', 'output': []}},
            {'type': 'response.output_item.added', 'output_index': 0, 'item': item},
            {'type': 'response.output_item.done', 'output_index': 0, 'item': item},
            {'type': 'response.completed', 'response': {'id': 'resp_fixture', 'object': 'response',
             'status': 'completed', 'output': [item],
             'usage': {'input_tokens': 1, 'output_tokens': 1, 'total_tokens': 2}}},
        ]
        body = ''.join('data: ' + json.dumps(e) + '\n\n' for e in events).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def probe(fixed, fail_server=False):
    server = ThreadingHTTPServer(('127.0.0.1', 0), Responses)
    server.outputs, server.requests = [], 0
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    original = CodexAppServerClient.__init__
    try:
        # Windows can briefly retain Codex's background plugin-cache file handles.
        with tempfile.TemporaryDirectory(prefix='codex-mcp-first-', ignore_cleanup_errors=True) as tmp:
            home = Path(tmp)
            script = home / 'mcp.py'
            script.write_text('raise SystemExit(1)\n' if fail_server else MCP_FIXTURE)
            (home / 'config.toml').write_text(
                'model_provider="fixture"\nmodel="gpt-5.6-luna"\n'
                '[model_providers.fixture]\nname="fixture"\n'
                f'base_url="http://127.0.0.1:{server.server_port}"\n'
                'wire_api="responses"\nrequires_openai_auth=false\n'
                '[mcp_servers.hermes-tools]\n'
                f'command={json.dumps(sys.executable)}\nargs=[{json.dumps(str(script))}]\n')
            with patch.object(CodexAppServerClient, '__init__', wrap_client_init(original) if fixed else original):
                with ExitStack() as stack:
                    client = CodexAppServerClient(codex_bin=os.environ['CODEX_BINARY'], codex_home=tmp)
                    stack.callback(client._proc.stdout.close)
                    stack.callback(client._proc.stderr.close)
                    stack.enter_context(client)
                    client.initialize(client_name='hermes-test', client_version='1')
                    if fail_server:
                        try:
                            client.request('thread/start', {'cwd': tmp}, timeout=15)
                        except CodexAppServerError:
                            assert server.requests == 0, 'Model started despite failed required MCP'
                            return
                        raise AssertionError('Required MCP failure did not block thread startup')
                    tid = client.request('thread/start', {'cwd': tmp}, timeout=15)['thread']['id']
                    client.request('turn/start', {'threadId': tid, 'input': [{'type': 'text', 'text': 'Say READY.'}],
                                   'model': 'gpt-5.6-luna', 'effort': 'medium'}, timeout=15)
                    deadline = time.monotonic() + 20
                    while time.monotonic() < deadline:
                        event = client.take_notification(timeout=.1)
                        if event and event.get('method') == 'turn/completed':
                            break
                    else:
                        raise AssertionError('Fixture turn did not finish')
                    assert server.outputs, 'First-turn code-mode discovery was not executed'
                    return json.dumps(server.outputs)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


@unittest.skipUnless(os.environ.get('CODEX_BINARY'), 'Set CODEX_BINARY for real app-server tests')
class FirstTurnTests(unittest.TestCase):
    def test_delayed_mcp_is_available_in_first_code_mode_call(self):
        self.assertNotIn('mcp__hermes_tools__a2a_call', probe(False))
        self.assertIn('mcp__hermes_tools__a2a_call', probe(True))

    def test_failed_required_server_prevents_model_start(self):
        probe(True, fail_server=True)


if __name__ == '__main__':
    unittest.main()
