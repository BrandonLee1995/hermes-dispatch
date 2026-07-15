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

## HTTP MCP container role

The `http-mcp` service intentionally uses a direct Python `entrypoint` and runs
as `${HERMES_UID}:${HERMES_GID}` instead of using the Hermes image default
`/init` entrypoint. The Hermes container boot reconciler restores any gateway
whose shared `/opt/data/gateway_state.json` has `desired_state=running`; when
`gateway` and `http-mcp` share `/home/marvel/.hermes`, letting both containers
run `/init` starts two gateway/WhatsApp bridge processes against the same
WhatsApp session.

The symptom is a repeating WhatsApp bridge conflict with `type=replaced`,
followed by `Not connected to WhatsApp`. Keeping `http-mcp` as a supervisor-only
container makes the gateway container the single owner of the WhatsApp bridge.
Rollback is to restore the previous compose backup and recreate `http-mcp`, but
that will reintroduce duplicate bridge ownership if WhatsApp is enabled.

Runtime credentials stay in `.env` and must not be committed.
