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
- requester-bound approval buttons for shared group sessions

The plugin should be enabled only for QQ Bot deployments that need those
workarounds.

## Codex App-Server Area

`plugins/codex-app-server-phase-hotfix` is the mounted compatibility boundary
between Hermes' Codex runtime and Gateway adapters. It has four independent,
removable patches:

- phase-aware interim/final message routing
- `imageGeneration` projection into normal gateway media delivery
- blocking approval routing through Hermes' existing per-session queue
- Computer Use application authorization through MCP elicitation

The approval bridge does not implement a second authorization database. During
an active Gateway turn it resolves the current session key, reuses the notifier
already registered by `gateway/run.py`, and waits on `tools.approval` exactly as
Hermes terminal tools do. QQ remains only a presentation and interaction
adapter; clicking its approval buttons resolves the same Gateway queue.

Codex owns permission scope. The bridge grants at most the requested permission
subset, maps one-shot approval to turn scope, maps the QQ persistent choice to
Codex session scope, and represents deny/timeout as an empty subset. No approval
changes `config.toml`, container source, or a host permanent allowlist.

Computer Use remains the policy owner. The bridge recognizes only the bundled
`computer-use` connector, forwards its app authorization elicitation, and sends
the accepted persistence scope back to Codex. Hard runtime safety denials are
not converted into prompts or bypassed.

For QQ shared groups, the adapter cannot infer an approver from the shared
session key. `qqbot-connect-hotfix` therefore issues a one-time opaque button
token bound to the current turn's `HERMES_SESSION_USER_ID`. The real session key
and requester identity remain in memory; only the matching operator in the
matching group can consume the token. This is an interaction-routing record,
not a durable permission database. Typed `/approve` and `/deny` commands consult
the same requester record.

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
