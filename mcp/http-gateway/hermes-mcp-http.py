# /// script
# requires-python = ">=3.13"
# ///
"""Run Hermes' built-in MCP bridge over streamable HTTP.

How to run:
    HERMES_MCP_HTTP_BEARER_TOKEN='<token>' \
    HERMES_MCP_HTTP_HOST=127.0.0.1 HERMES_MCP_HTTP_PORT=8765 \
        python /opt/data/scripts/hermes-mcp-http.py
"""

from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, "/opt/hermes")
sys.path.insert(0, "/opt/data/scripts")

from mcp_serve import EventBridge, create_mcp_server  # noqa: E402
from hermes_mcp_http_auth import BearerTokenAuthMiddleware  # noqa: E402


class MissingBearerTokenError(RuntimeError):
    """Raised when the HTTP MCP wrapper starts without a bearer token."""


def env_flag(name: str, *, default: bool = False) -> bool:
    """Read a boolean environment flag."""
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().casefold() in {"1", "true", "yes", "on"}


def require_bearer_token() -> str:
    """Read the bearer token required for all HTTP MCP requests."""
    token = os.getenv("HERMES_MCP_HTTP_BEARER_TOKEN", "").strip()
    if not token:
        raise MissingBearerTokenError(
            "HERMES_MCP_HTTP_BEARER_TOKEN must be set before exposing Hermes MCP over HTTP.",
        )
    return token


def install_optional_hotfixes() -> None:
    """Install instance-specific compatibility hotfixes when explicitly enabled."""
    if not env_flag("HERMES_MCP_ENABLE_QQBOT_HOTFIX"):
        return

    from hermes_mcp_qqbot_target_patch import install as install_qqbot_target_patch

    install_qqbot_target_patch()


logging.basicConfig(level=logging.INFO)
install_optional_hotfixes()

bridge = EventBridge()
bridge.start()

server = create_mcp_server(event_bridge=bridge)
host = os.getenv("HERMES_MCP_HTTP_HOST", "127.0.0.1")
port = int(os.getenv("HERMES_MCP_HTTP_PORT", "8765"))
streamable_http_path = os.getenv("HERMES_MCP_HTTP_PATH", "/mcp")
server.settings.host = host
server.settings.port = port
server.settings.streamable_http_path = streamable_http_path
server.settings.transport_security = None

try:
    import uvicorn

    app = server.streamable_http_app()
    protected_app = BearerTokenAuthMiddleware(app=app, expected_token=require_bearer_token())
    uvicorn.run(
        protected_app,
        host=host,
        port=port,
        log_level=server.settings.log_level.lower(),
    )
finally:
    bridge.stop()
