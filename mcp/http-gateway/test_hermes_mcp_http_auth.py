"""Verify bearer-token parsing for the Hermes HTTP MCP wrapper."""

from __future__ import annotations

from hermes_mcp_http_auth import extract_bearer_token, is_authorized


def test_extract_bearer_token_when_header_is_valid() -> None:
    token = extract_bearer_token("Bearer secret-token")

    assert token == "secret-token"


def test_extract_bearer_token_when_scheme_case_differs() -> None:
    token = extract_bearer_token("bearer secret-token")

    assert token == "secret-token"


def test_extract_bearer_token_when_header_is_invalid() -> None:
    token = extract_bearer_token("Basic secret-token")

    assert token is None


def test_is_authorized_when_token_matches() -> None:
    headers = [(b"authorization", b"Bearer secret-token")]

    assert is_authorized(headers, "secret-token")


def test_is_authorized_when_token_differs() -> None:
    headers = [(b"authorization", b"Bearer wrong-token")]

    assert not is_authorized(headers, "secret-token")
