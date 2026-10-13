#!/usr/bin/env python3
"""Read-only real-model ablation; raw KB content remains on the owning host."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import random
import sys
import time
from types import SimpleNamespace


def load_plugin(source):
    spec = importlib.util.spec_from_file_location('weknora_ablation_plugin', source / '__init__.py',
                                                 submodule_search_locations=[str(source)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run(home, source, output, repetitions, smoke=False, selected_mode=None):
    from dotenv import dotenv_values
    import yaml
    from hermes_cli.env_loader import load_hermes_dotenv
    from agent.transports.codex_app_server import CodexAppServerClient
    home = home.resolve()
    os.environ['HERMES_HOME'] = str(home)
    load_hermes_dotenv(hermes_home=home)
    env = dotenv_values(home / '.env')
    codex_home = env.get('CODEX_HOME')
    cfg = yaml.safe_load((home / 'config.yaml').read_text(encoding='utf-8'))
    model = cfg['model']['default']
    mcp_cfg = cfg.get('mcp_servers') or {}
    assert any('weknora' in name.casefold() for name in mcp_cfg), 'No WeKnora profile config'
    plugin = load_plugin(source)
    plugin.register(SimpleNamespace(register_hook=lambda *a: None, get_config=lambda *a: 'off'))
    cases = json.loads(Path(__file__).with_name('cases.json').read_text(encoding='utf-8'))
    if smoke:
        cases, repetitions = cases[:1], 1
    output.mkdir(parents=True, exist_ok=True)
    cwd = output / 'empty-cwd'
    cwd.mkdir(exist_ok=True)
    # Restrict the experiment's MCP surface without mutating the actual profile.
    import tomllib
    cc = Path(codex_home or Path.home() / '.codex') / 'config.toml'
    coder = tomllib.loads(cc.read_text(encoding='utf-8')) if cc.exists() else {}
    extra = ['-c', 'web_search="disabled"', '-c', 'agents.enabled=false']
    for name in coder.get('mcp_servers', {}):
        if 'weknora' not in name.casefold():
            extra += ['-c', 'mcp_servers.' + name + '.enabled=false']
        else:
            extra += ['-c', 'mcp_servers.' + name + '.enabled_tools=' + json.dumps(sorted(plugin.SEARCH | plugin.READ))]
    jobs = [(rep, mode, case) for rep in range(repetitions)
            for mode in ([selected_mode] if selected_mode else ['off', 'hermes', 'codex', 'both']) for case in cases]
    random.Random(20261007).shuffle(jobs)
    clients, scopes, rows = {}, {}, []
    try:
        for rep, mode, case in jobs:
            scope = scopes.setdefault(mode, {'agent': None, 'mode': mode, 'home': home,
                     'path': home / 'state/weknora-routing' / ('ablation-' + mode + '.json')})
            scope['original_message'] = case['prompt']
            token = plugin._SCOPE.set(scope)
            row = {'case': case['id'], 'mode': mode, 'repeat': rep,
                   'expected_search': case['expected_search'], 'model': model,
                   'search_calls': 0, 'successful_searches': 0, 'hook_contexts': 0,
                   'hook_failures': 0, 'other_tool_calls': 0, 'tool_outcomes': [], 'server_requests': []}
            start = time.monotonic()
            try:
                if mode not in clients:
                    client = CodexAppServerClient(codex_bin=str(Path.home() / '.local/bin/codex'),
                        codex_home=codex_home, extra_args=extra,
                        env={'HERMES_HOME': str(home)})
                    client.initialize(timeout=30)
                    clients[mode] = client
                    listed = client.request('hooks/list', {'cwds': [str(cwd)]})
                    own = [h for x in listed.get('data', []) for h in x.get('hooks', [])
                           if str(source / 'codex_hook.py') in h.get('command', '')]
                    if mode in {'codex', 'both'}:
                        assert len(own) == 1 and own[0]['trustStatus'] == 'trusted', 'Hook trust not applied'
                client = clients[mode]
                thread = client.request('thread/start', {'cwd': str(cwd), 'model': model,
                    'ephemeral': True, 'approvalPolicy': 'on-request', 'sandbox': 'read-only'}, timeout=30)['thread']['id']
                # Discard unrelated startup notifications from earlier threads.
                while client.take_notification(timeout=0) is not None:
                    pass
                turn_start = time.monotonic()
                response = client.request('turn/start', {'threadId': thread,
                    'input': [{'type': 'text', 'text': case['prompt']}], 'model': model,
                    'effort': 'medium'}, timeout=40)
                turn = response['turn']['id']
                seen = set()
                finished = False
                deadline = time.monotonic() + 120
                while time.monotonic() < deadline:
                    request = client.take_server_request(timeout=0)
                    if request:
                        row['server_requests'].append({'method':request.get('method'), 'param_keys':list((request.get('params') or {})), 'schema':(request.get('params') or {}).get('requestedSchema'), 'meta_keys':list(((request.get('params') or {}).get('_meta') or {}).keys())})
                        # Preserve native review; never approve shell/file/credential requests.
                        if request.get('method') == 'mcpServer/elicitation/request':
                            client.respond(request['id'], {'action':'decline','content':None,'_meta':None})
                        elif 'requestApproval' in request.get('method', ''):
                            client.respond(request['id'], {'decision':'decline'})
                        else:
                            client.respond_error(request['id'], code=-32601, message='Unsupported experiment request')
                    note = client.take_notification(timeout=.5)
                    if not note:
                        continue
                    params = note.get('params') or {}
                    if params.get('threadId') not in {None, thread}:
                        continue
                    method = note.get('method', '')
                    if method == 'hook/completed':
                        h = params.get('run') or {}
                        if h.get('source') == 'sessionFlags' and h.get('eventName') == 'userPromptSubmit':
                            row['hook_contexts'] += sum(e.get('kind') == 'context' and bool(e.get('text')) for e in h.get('entries', []))
                            row['hook_failures'] += h.get('status') != 'completed'
                    if method == 'item/completed':
                        item = params.get('item') or {}
                        if item.get('type') == 'mcpToolCall' and item.get('id') not in seen:
                            seen.add(item.get('id'))
                            raw_name = str(item.get('tool', '')).split('__')[-1]
                            result = item.get('result') or {}
                            outcome = {'tool': raw_name, 'status': item.get('status'), 'is_error': bool(item.get('error') or result.get('isError', False))}
                            for content in result.get('content', []):
                                if content.get('type') == 'text':
                                    try:
                                        body = json.loads(content.get('text', ''))
                                        if isinstance(body, dict):
                                            outcome['response_keys'] = list(body)
                                            outcome['data_count'] = len(body['data']) if isinstance(body.get('data'), list) else None
                                            outcome['reported_error'] = bool(body.get('error') or body.get('success') is False)
                                    except (ValueError, TypeError):pass
                            error = item.get('error')
                            if error:
                                import re
                                safe = re.sub(r'https?://\S+', '[url]', str(error))
                                safe = re.sub(r'\b[A-Za-z0-9_.-]{32,}\b', '[redacted]', safe)
                                outcome['error_summary'] = safe[:500]
                            row['tool_outcomes'].append(outcome)
                            if raw_name in plugin.SEARCH:
                                row['search_calls'] += 1
                                result = item.get('result') or {}
                                row['successful_searches'] += (item.get('status') == 'completed'
                                    and not item.get('error') and not result.get('isError', False))
                            elif item.get('type') == 'mcpToolCall':
                                row['other_tool_calls'] += 1
                    if method == 'turn/completed' and (params.get('turn') or {}).get('id') == turn:
                        row['turn_status'] = params['turn'].get('status')
                        finished = True
                        break
                if not finished:
                    row['turn_status'] = 'timeout'
                    client.request('turn/interrupt', {'threadId': thread, 'turnId': turn}, timeout=10)
                row['seconds'] = round(time.monotonic() - turn_start, 3)
                row['tools_available'] = bool(scope.get('last_tools'))
            except Exception as error:
                row['error_type'] = type(error).__name__
                row['turn_status'] = 'error'
                row['seconds'] = round(time.monotonic() - start, 3)
                if mode in clients:
                    clients.pop(mode).close()
            finally:
                plugin._SCOPE.reset(token)
                scope['path'].unlink(missing_ok=True)
            rows.append(row)
            # Persist metrics only, never tool arguments, raw references, or answers.
            (output / 'results.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2) + '\n')
            print(json.dumps(row, ensure_ascii=False), flush=True)
    finally:
        for client in clients.values():
            client.close()
    (output / 'finished.json').write_text(json.dumps({'calls': len(rows), 'model': model}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repetitions', type=int, default=3)
    parser.add_argument('--smoke', action='store_true')
    parser.add_argument('--mode', choices=['off','hermes','codex','both'])
    args = parser.parse_args()
    run(args.home, args.source, args.output, args.repetitions, args.smoke, args.mode)
