"""Regression for real native WeKnora consent and the actual Hermes queue."""
import copy
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('approval_bridge_under_test', Path(__file__).with_name('approval_bridge.py'))
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)

request = {
    'id': 1, 'method': 'mcpServer/elicitation/request',
    'params': {
        'serverName': 'weknora', 'mode': 'form', 'threadId': 'diagnostic',
        'message': 'Allow the weknora MCP server to run tool "hybrid_search"?',
        'requestedSchema': {'type': 'object', 'properties': {}},
        '_meta': {'codex_approval_kind': 'mcp_tool_call',
                  'tool_params': {'query': '报销流程', 'match_count': 1}},
    },
}

def main():
    from agent.transports.codex_app_server_session import CodexAppServerSession
    from tools import approval
    upstream = CodexAppServerSession._handle_server_request
    assert not bridge._upstream_handles_mcp_tool_confirmation(CodexAppServerSession, upstream)
    handler = bridge.wrap_server_request_handler(upstream)
    assert bridge._upstream_handles_mcp_tool_confirmation(CodexAppServerSession, handler)
    responses, notified = [], []
    session = object.__new__(CodexAppServerSession)
    session._client = SimpleNamespace(respond=lambda rid, result: responses.append(result))
    session._approval_callback = bridge._gateway_approval_choice
    key = 'mcp-consent-local-regression'
    token = approval.set_current_session_key(key)
    choice = 'once'

    def notify(data):
        notified.append(data)
        assert data['codex_approval_choices'] == ['once', 'deny']
        assert data['allow_session'] is False and data['allow_permanent'] is False
        assert '报销流程' in data['command']
        assert approval.resolve_gateway_approval(key, choice) == 1

    approval.register_gateway_notify(key, notify)
    try:
        for choice in ['once', 'deny', 'session', 'always']:
            handler(session, request)
            assert responses[-1]['action'] == ('accept' if choice == 'once' else 'decline')
            with approval._lock:
                assert not approval._gateway_queues.get(key)
        approval.unregister_gateway_notify(key)
        handler(session, request)
        assert responses[-1]['action'] == 'decline'
        def failing_notify(data):
            raise RuntimeError('local test delivery failure')
        approval.register_gateway_notify(key, failing_notify)
        handler(session, request)
        assert responses[-1]['action'] == 'decline'
        with approval._lock:
            assert not approval._gateway_queues.get(key)
    finally:
        approval.unregister_gateway_notify(key)
        approval.reset_current_session_key(token)

    delegated = []
    narrow_handler = bridge.wrap_server_request_handler(lambda s, req: delegated.append(req))
    for mutate in [
        lambda p: p.update(mode='url'),
        lambda p: p.update(mode={'unexpected': True}),
        lambda p: p.update(serverName='hermes-tools'),
        lambda p: p.update(_meta={}),
        lambda p: p.update(requestedSchema={'type': 'object', 'properties': {'secret': {'type': 'string'}}}),
        lambda p: p.update(requestedSchema={'type': 'object', 'properties': {}, 'required': ['secret']}),
    ]:
        req = copy.deepcopy(request)
        mutate(req['params'])
        narrow_handler(session, req)
        assert delegated[-1] == req
    print('MCP consent: real queue accept/deny, scope limits, missing notifier, delivery failure, cleanup, structured/OAuth delegation PASS')

if __name__ == '__main__':
    main()
