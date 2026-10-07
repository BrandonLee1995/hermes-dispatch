"""Runnable profile/capability/Hook isolation check; never contacts production."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

source = Path(__file__).parent
spec = importlib.util.spec_from_file_location('wkn_routing_test', source / '__init__.py',
                                             submodule_search_locations=[str(source)])
plugin = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = plugin
spec.loader.exec_module(plugin)
p = sys.modules['wkn_routing_test.policy']
s = sys.modules['wkn_routing_test.codex_support']
sys.path.insert(0, str(source))
import codex_hook as c


def main():
    configured = {'weknora': {'url': 'https://example.invalid/mcp/private',
                              'headers': {'Authorization': 'Bearer test-scope'}}}
    effective = {'weknora': {'url': 'https://example.invalid/mcp/private',
                             'http_headers': {'authorization': 'Bearer test-scope'}}}
    status = {'data': [{'name': 'weknora', 'tools': {'hybrid_search': {},
                          'list_knowledge_bases': {}, 'delete_knowledge': {}}}]}
    tools = p.codex_tools(configured, effective, status, {})
    assert [x['tool'] for x in tools] == ['hybrid_search', 'list_knowledge_bases']
    assert 'CURRENT TURN ONLY' in p.render(tools)
    assert p.render([{'tool': 'list_knowledge_bases', 'name': 'kb'}]) == ''
    for value in [False, 'false', '0', None]:
        bad = {'weknora': {**configured['weknora'], 'enabled': value}}
        assert p.codex_tools(bad, effective, status, {}) == []
    for change in [{'url': 'https://example.invalid/mcp/OTHER'},
                   {'http_headers': {'authorization': 'Bearer OTHER'}}]:
        bad = {'weknora': {**effective['weknora'], **change}}
        assert p.codex_tools(configured, bad, status, {}) == []
    assert p.codex_tools({'weknora': {}}, effective, status, {}) == []
    assert p.codex_tools(configured, effective, {'data': []}, {}) == []
    assert p.codex_tools(configured, {'weknora': {**effective['weknora'], 'enabled_tools': []}}, status, {}) == []
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp).resolve()
        file = home / 'state/weknora-routing/test.json'
        event = {'hook_event_name': 'UserPromptSubmit', 'prompt': '公司流程？'}
        env = {'HERMES_HOME': str(home), c.CAPS_ENV: str(file)}
        s.write_capabilities(file, home, 'codex', tools, event['prompt'])
        assert c.context(event, env, p.source_digest()) == p.render(tools)
        assert c.context(event, env, 'changed-code') == ''
        assert c.context({**event, 'prompt': 'OTHER request'}, env, p.source_digest()) == ''
        assert c.context(event, {'HERMES_HOME': str(home / 'OTHER'), c.CAPS_ENV: str(file)}, p.source_digest()) == ''
        assert c.context({**event, 'hook_event_name': 'Stop'}, env, p.source_digest()) == ''
        original = json.loads(file.read_text())
        for mutation in [{'mode': 'hermes'}, {'mode': 'off'}, {'origin': 'internal'},
                         {'platform': 'api_server'}, {'expires': time.time() - 1},
                         {'created': original['created'] - 1}, {'pid': -99999}]:
            file.write_text(json.dumps({**original, **mutation}))
            assert c.context(event, env, p.source_digest()) == ''
        opts = s.hook_options(sys.executable, home, file)
        assert '--source-sha256' in opts[1] and '--capabilities-file' in opts[1]
        assert not any('dangerously-bypass' in x for x in opts)
    # Legacy Hook is a no-op outside its authorized QQ caller context.
    assert plugin.pre_llm_call(platform='qqbot', user_message='公司流程？') is None
    token = plugin._SCOPE.set({'mode': 'hermes', 'agent': type('Agent', (), {'api_mode': 'chat_completions'})()})
    try:
        with patch.object(plugin, 'legacy_tools', return_value=tools):
            assert plugin.pre_llm_call()['context'] == p.render(tools)
        with patch.object(plugin, 'legacy_tools', return_value=[]):
            assert plugin.pre_llm_call() is None
    finally:
        plugin._SCOPE.reset(token)
    # Exercise the actual decorated prologue and Codex boundary without models.
    from agent import turn_context, codex_runtime
    from gateway.session_context import set_session_vars, clear_session_vars
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    from types import SimpleNamespace
    original_build = turn_context.build_turn_context
    original_codex = codex_runtime.run_codex_app_server_turn
    captured = []
    def prologue(agent, message, *args, **kwargs):
        captured.append(plugin._SCOPE.get())
        return message
    def codex_turn(agent, *args, **kwargs):
        captured.append(plugin._SCOPE.get())
        return 'done'
    settings = {'mode': 'both'}
    ctx = SimpleNamespace(register_hook=lambda *a: None, get_config=lambda key, default: settings.get(key, default))
    with tempfile.TemporaryDirectory() as temporary:
        home = Path(temporary).resolve()
        actor = SimpleNamespace(platform='qqbot', _user_id='cached-sender', api_mode='codex_app_server')
        turn_context.build_turn_context = prologue
        codex_runtime.run_codex_app_server_turn = codex_turn
        plugin.register(ctx)
        real_configured = plugin.configured
        plugin.configured = lambda: configured
        ht = set_hermes_home_override(home)
        st = set_session_vars(platform='qqbot', chat_id='test-group', chat_type='group', user_id='test-user')
        try:
            message = '公司流程？'
            assert turn_context.build_turn_context(actor, message) == message
            assert captured[-1]['home'] == home and captured[-1]['original_message'] == message
            assert plugin._SCOPE.get() is None
            assert codex_runtime.run_codex_app_server_turn(actor) == 'done'
            assert captured[-1]['original_message'] == message
            assert actor._weknora_turn_scope is None and plugin._SCOPE.get() is None
            turn_context.build_turn_context(actor, '/model --global @机器人')
            assert captured[-1] is None
        finally:
            clear_session_vars(st)
            reset_hermes_home_override(ht)
        # Engaged context is authoritative: never reuse a cached QQ sender.
        st = set_session_vars(platform='qqbot', chat_id='test-group', chat_type='group', user_id='')
        try:
            turn_context.build_turn_context(actor, 'background completion')
            assert captured[-1] is None
        finally:
            clear_session_vars(st)
        st = set_session_vars(platform='qqbot', chat_id='test-private', chat_type='dm', user_id='test-user')
        ht = set_hermes_home_override(home)
        try:
            turn_context.build_turn_context(actor, '查询公司资料')
            assert captured[-1] is not None
        finally:
            clear_session_vars(st)
            reset_hermes_home_override(ht)
    plugin.configured = real_configured
    turn_context.build_turn_context = original_build
    codex_runtime.run_codex_app_server_turn = original_codex
    print('WeKnora: disabled/disconnected/no retrieval, endpoint isolation, stale/changed/prompt-bound Hook data, legacy contexts PASS')


if __name__ == '__main__':
    main()
