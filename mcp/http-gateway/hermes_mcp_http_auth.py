"""Bearer-token authentication for the Hermes streamable HTTP MCP wrapper."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from hmac import compare_digest
from typing import Final

from starlette.types import Receive, Scope, Send

AUTHORIZATION_HEADER: Final = b"authorization"
BEARER_SCHEME: Final = "bearer"

ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


def extract_bearer_token(authorization_header: str) -> str | None:
    """Return the bearer token from an Authorization header."""
    parts = authorization_header.strip().split(None, 1)
    if len(parts) != 2:
        return None

    scheme, token = parts
    if scheme.casefold() != BEARER_SCHEME:
        return None

    token = token.strip()
    if not token:
        return None
    return token


def is_authorized(headers: list[tuple[bytes, bytes]], expected_token: str) -> bool:
    """Check whether request headers contain the configured bearer token."""
    for name, value in headers:
        if name.lower() != AUTHORIZATION_HEADER:
            continue
        token = extract_bearer_token(value.decode("latin-1"))
        return token is not None and compare_digest(token, expected_token)
    return False


@dataclass(frozen=True, slots=True)
class BearerTokenAuthMiddleware:
    """ASGI middleware that rejects HTTP requests without a bearer token."""

    app: ASGIApp
    expected_token: str

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = scope.get("headers", [])
        if is_authorized(headers, self.expected_token):
            await self.app(scope, receive, send)
            return

        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"text/plain; charset=utf-8"),
                    (b"www-authenticate", b'Bearer realm="hermes-mcp"'),
                ],
            },
        )
        await send(
            {
                "type": "http.response.body",
                "body": b"Unauthorized\n",
            },
        )
