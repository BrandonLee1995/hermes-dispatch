# MCP seed area

Place department-safe MCP server definitions or bootstrap files here.

This directory is copied to `/public/hermes/<dept>/mcp` only when that target
directory is empty. Keep secrets out of this repository. Put per-department
tokens in the department `/public/hermes/<dept>/.env` file or in a secret
manager.

The common Tencent Docs MCP server is pre-seeded in `/opt/data/config.yaml`
under `mcp_servers.tencent-docs`, not as an active secret-bearing file in this
directory. The endpoint is fixed to Tencent's official MCP URL:

```text
https://docs.qq.com/openapi/mcp
```

Each department only needs to set:

```text
TENCENT_DOCS_TOKEN=<department or company token>
```

The server is enabled by default. Set the token before using Tencent Docs tools,
then test it through the Hermes dashboard or `hermes mcp`.

Note: Codex MCP config commonly uses `http_headers`, but the current Hermes
runtime reads HTTP MCP headers from the `headers` key. Keep the server name
`tencent-docs` and environment variable names aligned with the company config,
but do not rename `headers` to `http_headers` in Hermes `config.yaml`.
