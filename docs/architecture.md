# Architecture

## Principle

`hermes-dispatch` treats the Hermes container image as immutable. Compatibility
code is mounted through `/opt/data` and activated by config or plugin discovery.
This keeps fixes durable across container recreation and makes every workaround
removable when upstream behavior changes.

## QQ Bot Area

`plugins/qqbot-connect-hotfix` is a user plugin that patches the built-in QQ Bot
adapter at runtime. It covers local integration gaps observed in group and DM
delivery:

- connect signature compatibility
- channel directory lookup for chat type routing
- emoji-only group mentions
- group context buffering and deterministic compaction
- plain-text retry for markdown/body compatibility
- media send retry without incompatible captions

The plugin should be enabled only for QQ Bot deployments that need those
workarounds.

## WhatsApp Area

`plugins/whatsapp-bridge-policy-hotfix` separates three concerns that are often
mixed in the upstream bridge path:

- private chat authorization through `WHATSAPP_ALLOWED_USERS`
- group chat openness through `WHATSAPP_GROUP_POLICY`
- group response behavior through `WHATSAPP_REQUIRE_MENTION`

The target model is:

```text
WHATSAPP_DM_POLICY=allowlist
WHATSAPP_ALLOWED_USERS=<private-dm-users>
WHATSAPP_GROUP_POLICY=open
WHATSAPP_ALLOW_ALL_USERS=true
WHATSAPP_REQUIRE_MENTION=true
```

`WHATSAPP_ALLOW_ALL_USERS=true` is only the required explicit opt-in for open
group policy. With `WHATSAPP_DM_POLICY=allowlist`, private chats still require
`WHATSAPP_ALLOWED_USERS`.

## MCP Area

`mcp/http-gateway` exposes Hermes MCP over streamable HTTP with bearer-token
authentication. It is intended for local network reverse proxying through Caddy
or Cloudflare Tunnel while keeping authentication inside the Python wrapper, so
loopback access and proxied access share the same authorization path.

The optional QQBot target patch can be enabled for deployments where MCP
outbound dispatch needs QQBot target normalization.

## Deployment Area

`deploy/mwe-support-dev` is an example deployment shape:

- Hermes gateway container
- HTTP MCP wrapper container
- Caddy reverse proxy
- optional Cloudflare Tunnel

Copy it and provide a local `.env` derived from `.env.example`. Do not commit
filled runtime env files.
