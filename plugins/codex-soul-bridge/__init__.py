"""Append profile SOUL only inside a Hermes Gateway Codex runtime call."""
from contextvars import ContextVar
from functools import wraps

_SOUL = ContextVar('hermes_codex_profile_soul', default=None)
_MARKER = '_hermes_codex_soul_bridge'


def agent_home(agent):
    from agent.system_prompt import _agent_home
    return _agent_home(agent)


def load_soul(home):
    from agent.prompt_builder import load_soul_md
    return load_soul_md(home_override=home)


def wrap_runtime_turn(original):
    @wraps(original)
    def run(agent, *args, **kwargs):
        soul = None
        if getattr(agent, '_gateway_session_key', None) and getattr(agent, 'session_id', None):
            home = agent_home(agent)
            # Never infer a persona from ambient HERMES_HOME in ordinary clients.
            if home is not None and home.is_absolute():
                soul = load_soul(home)
        token = _SOUL.set(soul)
        try:
            return original(agent, *args, **kwargs)
        finally:
            _SOUL.reset(token)
    setattr(run, _MARKER, True)
    return run


def wrap_request(original):
    @wraps(original)
    def request(self, method, params=None, *args, **kwargs):
        soul = _SOUL.get()
        # Resume overrides can be ignored for legacy threads; inject before their turn.
        if soul and method == 'thread/start':
            params = dict(params or {})
            existing = params.get('developerInstructions')
            if existing is None:
                existing = (params.get('config') or {}).get('developer_instructions')
            if existing is None:
                config = original(self, 'config/read', {
                    'cwd': params.get('cwd'), 'includeLayers': False,
                }, timeout=15)
                existing = config.get('config', {}).get('developer_instructions')
            if existing is not None and not isinstance(existing, str):
                raise TypeError('Codex developer instructions must be text')
            params['developerInstructions'] = '\n\n'.join(x for x in (existing, soul) if x)
        if soul and method == 'turn/start':
            thread_id = (params or {}).get('threadId')
            if thread_id:
                loaded = self.__dict__.setdefault('_hermes_soul_by_thread', {})
                if loaded.get(thread_id) != soul:
                    # Update the owning idle thread before starting its next model turn.
                    original(self, 'thread/inject_items', {
                        'threadId': thread_id,
                        'items': [{'type': 'message', 'role': 'developer',
                                   'content': [{'type': 'input_text', 'text': soul}]}],
                    }, timeout=15)
                    loaded[thread_id] = soul
        result = original(self, method, params, *args, **kwargs)
        if soul and method == 'thread/start':
            thread_id = (result.get('thread') or {}).get('id')
            if thread_id:
                self.__dict__.setdefault('_hermes_soul_by_thread', {})[thread_id] = soul
        return result
    setattr(request, _MARKER, True)
    return request


def register(ctx):
    from agent import codex_runtime
    from agent.transports.codex_app_server import CodexAppServerClient
    if not getattr(codex_runtime.run_codex_app_server_turn, _MARKER, False):
        codex_runtime.run_codex_app_server_turn = wrap_runtime_turn(codex_runtime.run_codex_app_server_turn)
    if not getattr(CodexAppServerClient.request, _MARKER, False):
        CodexAppServerClient.request = wrap_request(CodexAppServerClient.request)
