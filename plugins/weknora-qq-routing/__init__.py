"""Bind current QQ user turns; keep the original message and profile intact."""
import contextvars
import functools
import logging
import os
from pathlib import Path
import uuid

from .policy import MODES, SEARCH, READ, codex_tools, enabled, render
from .codex_support import hook_options, reviewed_hook_trust, write_capabilities

log = logging.getLogger(__name__)
_SCOPE = contextvars.ContextVar('weknora_qq_turn', default=None)
_MARK = '_weknora_qq_routing_wrapped'


def configured():
    from tools.mcp_tool import _load_mcp_config
    return {k: v for k, v in _load_mcp_config().items()
            if 'weknora' in k.casefold() and enabled(v.get('enabled', True))}


def legacy_tools(agent):
    from tools import mcp_tool as m
    # Exact config and provenance checks prevent a singleton MCP registry from
    # borrowing another profile's same-named server after a scope switch.
    expected = configured()
    from .policy import same_endpoint
    with m._lock:
        servers = dict(m._servers)
        owners = dict(m._mcp_tool_server_names)
    visible = {t.get('function', {}).get('name') for t in getattr(agent, 'tools', [])}
    rows = []
    for server_name, cfg in expected.items():
        server = servers.get(server_name)
        if not server or server.session is None or not server._ready.is_set():
            continue
        if not same_endpoint(cfg, server.config, os.environ):
            continue
        for raw in SEARCH | READ:
            name = m.mcp_prefixed_tool_name(server_name, raw)
            if name in visible and owners.get(name) == server_name:
                rows.append({'server': server_name, 'tool': raw, 'name': name})
    return sorted(rows, key=lambda x: x['name'])


def pre_llm_call(**kwargs):
    scope = _SCOPE.get()
    if not scope or scope['mode'] not in {'hermes', 'both'}:
        return
    # Codex has a separate early-return path that does not consume native
    # pre_llm_call context. Its adapter adds the same policy at turn/start.
    if getattr(scope['agent'], 'api_mode', '') == 'codex_app_server':
        return
    try:
        text = render(legacy_tools(scope['agent']))
        return {'context': text} if text else None
    except Exception:
        log.warning('WeKnora capability check unavailable; ordinary QQ flow retained')


def register(ctx):
    import sys
    from agent import turn_context, codex_runtime
    from agent.system_prompt import _agent_home
    from hermes_constants import get_hermes_home
    from gateway.session_context import get_session_env, session_context_engaged
    from agent.transports.codex_app_server import CodexAppServerClient

    ctx.register_hook('pre_llm_call', pre_llm_call)

    def scope_for(agent, message):
        if session_context_engaged():
            platform = get_session_env('HERMES_SESSION_PLATFORM', '')
            sender = get_session_env('HERMES_SESSION_USER_ID', '')
        else:
            platform, sender = getattr(agent, 'platform', ''), getattr(agent, '_user_id', '')
        platform = getattr(platform, 'value', platform)
        mode = ctx.get_config('mode', 'hermes')
        if not (platform == 'qqbot' and sender and not getattr(agent, '_parent_session_id', None)
                and mode in MODES and mode != 'off' and isinstance(message, str)
                and not message.lstrip().startswith('/')):
            return None
        home = Path(_agent_home(agent) or get_hermes_home()).resolve()
        if home != Path(get_hermes_home()).resolve():
            log.info('WeKnora routing skipped: profile scope mismatch')
            return None
        if not configured():
            log.info('WeKnora routing skipped: no enabled profile connection')
            return None
        path = getattr(agent, '_weknora_capabilities_path', None)
        if path is None:
            path = home / 'state/weknora-routing' / (uuid.uuid4().hex + '.json')
            agent._weknora_capabilities_path = path
        return {'agent': agent, 'mode': mode, 'home': home, 'path': path, 'original_message': message}

    original_build = turn_context.build_turn_context
    if not getattr(original_build, _MARK, False):
        @functools.wraps(original_build)
        def build(agent, user_message, *args, **kwargs):
            scope = scope_for(agent, user_message)
            agent._weknora_turn_scope = scope
            token = _SCOPE.set(scope)
            try:
                return original_build(agent, user_message, *args, **kwargs)
            finally:
                _SCOPE.reset(token)
        setattr(build, _MARK, True)
        turn_context.build_turn_context = build
        # Existing import aliases must reference the same decorated prologue.
        loop = sys.modules.get('agent.conversation_loop')
        if loop is not None and getattr(loop, 'build_turn_context', None) is original_build:
            loop.build_turn_context = build

    original_codex = codex_runtime.run_codex_app_server_turn
    if not getattr(original_codex, _MARK, False):
        @functools.wraps(original_codex)
        def codex_turn(agent, *args, **kwargs):
            scope = getattr(agent, '_weknora_turn_scope', None)
            token = _SCOPE.set(scope)
            try:
                return original_codex(agent, *args, **kwargs)
            finally:
                _SCOPE.reset(token)
                agent._weknora_turn_scope = None
                if scope:
                    scope['path'].unlink(missing_ok=True)
        setattr(codex_turn, _MARK, True)
        codex_runtime.run_codex_app_server_turn = codex_turn

    original_init = CodexAppServerClient.__init__
    original_request = CodexAppServerClient.request
    if not getattr(original_init, _MARK, False):
        @functools.wraps(original_init)
        def init(client, codex_bin='codex', codex_home=None, extra_args=None, env=None):
            scope = _SCOPE.get()
            args = list(extra_args or [])
            child_env = dict(env or {})
            if scope and scope['mode'] in {'codex', 'both'} and configured():
                import sys
                options = hook_options(sys.executable, scope['home'], scope['path'])
                token = _SCOPE.set(None)
                try:
                    # Listing definitions uses no model and no persistent writes.
                    def factory(probe_args):
                        return CodexAppServerClient(codex_bin=codex_bin, codex_home=codex_home,
                            extra_args=args + probe_args, env=child_env)
                    trusted, _ = reviewed_hook_trust(factory, options, scope['home'])
                except Exception:
                    log.warning('Native Codex routing Hook unavailable; no trust bypass used')
                    options, trusted = [], []
                finally:
                    _SCOPE.reset(token)
                args += options + trusted
            return original_init(client, codex_bin=codex_bin, codex_home=codex_home,
                                 extra_args=args, env=child_env)
        setattr(init, _MARK, True)
        CodexAppServerClient.__init__ = init

        @functools.wraps(original_request)
        def request(client, method, params=None, *args, **kwargs):
            scope = _SCOPE.get()
            if method == 'turn/start' and scope:
                parts = (params or {}).get('input') or []
                # Internal continuations/steering are not new user requests.
                if len(parts) == 1 and parts[0].get('type') == 'text' and parts[0].get('text') == scope['original_message']:
                    try:
                        config = original_request(client, 'config/read', {'includeLayers': False}).get('config', {})
                        status = original_request(client, 'mcpServerStatus/list', {})
                        tools = codex_tools(configured(), config.get('mcp_servers') or {}, status, os.environ)
                        scope['last_tools'] = tools
                        text = render(tools)
                        log.info('WeKnora routing capability mode=%s tools=%d context=%s',
                                 scope['mode'], len(tools), bool(text))
                        new_text = parts[0]['text']
                        if text and scope['mode'] in {'hermes', 'both'}:
                            new_text += '\n\n' + text
                        if scope['mode'] in {'codex', 'both'}:
                            write_capabilities(scope['path'], scope['home'], scope['mode'], tools, new_text)
                        params = {**params, 'input': [{**parts[0], 'text': new_text}]}
                    except Exception:
                        log.warning('WeKnora routing unavailable; original request retained')
            return original_request(client, method, params, *args, **kwargs)
        setattr(request, _MARK, True)
        CodexAppServerClient.request = request
