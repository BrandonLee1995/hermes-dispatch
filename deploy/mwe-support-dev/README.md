# mwe-support Hermes dev deployment example

This is the example shape used for the mwe-support dev instance. Copy this
directory to the target host and create a local `.env` from `.env.example`.

Reference target:

- Host: `marvel-mini-pc`
- Host user: `marvel`
- Data directory: `/home/marvel/.hermes`
- Service directory: `/home/marvel/hermes-mwe-support-dev`
- Department id: `mwe-support`
- Public dashboard hostname: `hermes-mwe-support-dev.nakroyn8n.top`

This intentionally differs from the production department layout under
`/public/hermes/<dept>` because it is a personal dev/test instance. It still
uses the same gateway + HTTP MCP + Caddy + Cloudflare Tunnel shape.

Runtime credentials stay in `.env` and must not be committed.
