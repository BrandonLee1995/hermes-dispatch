# /// script
# requires-python = ">=3.13"
# ///
"""List tools from a streamable HTTP MCP endpoint.

How to run:
    MCP_URL=http://<mcp-host>:18080/mcp \
    MCP_HOST_HEADER=<mcp-host>:18080 \
    MCP_BEARER_TOKEN='<token>' \
        python /opt/data/scripts/test_mcp_streamable.py
"""

from __future__ import annotations

import os

import anyio
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main() -> None:
    url = os.environ["MCP_URL"]
    host_header = os.environ.get("MCP_HOST_HEADER", "")
    bearer_token = os.environ.get("MCP_BEARER_TOKEN", "")
    headers = {}
    if host_header:
        headers["Host"] = host_header
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"

    async with streamablehttp_client(url, headers=headers or None, timeout=10) as (
        read_stream,
        write_stream,
        _get_session_id,
    ):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [tool.name for tool in tools.tools]
            print(f"tool_count={len(names)}")
            print("tools=" + ",".join(names))


anyio.run(main)
