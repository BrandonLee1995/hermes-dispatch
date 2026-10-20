"""Keep Hermes' MCP tools in the first Codex turn after a cold start."""
from functools import wraps
import os
from pathlib import Path
import tomllib

_MARKER = '_a2a_required_mcp_startup'


def wrap_client_init(original):
    @wraps(original)
    def init(self, codex_bin='codex', codex_home=None, extra_args=None, env=None, **kwargs):
        home = codex_home or (env or {}).get('CODEX_HOME') or os.environ.get('CODEX_HOME', '~/.codex')
        # shortcut: inspect the main Codex config; extend if Hermes MCP moves to a project-only layer.
        try:
            config = tomllib.loads((Path(home).expanduser() / 'config.toml').read_text(encoding='utf-8'))
        except FileNotFoundError:
            config = {}
        server = config.get('mcp_servers', {}).get('hermes-tools')
        if isinstance(server, dict) and server.get('enabled', True) and not server.get('required'):
            # Optional MCPs get only a short grace period before the first tool catalog.
            extra_args = [*(extra_args or []), '-c', 'mcp_servers.hermes-tools.required=true']
        return original(self, codex_bin=codex_bin, codex_home=codex_home,
                        extra_args=extra_args, env=env, **kwargs)
    setattr(init, _MARKER, True)
    return init


def install():
    from agent.transports.codex_app_server import CodexAppServerClient
    if not getattr(CodexAppServerClient.__init__, _MARKER, False):
        CodexAppServerClient.__init__ = wrap_client_init(CodexAppServerClient.__init__)
