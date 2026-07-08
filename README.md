# hermes-dispatch

Persistent compatibility layers for Hermes official containers.

This repository is for behavior that the upstream `nousresearch/hermes-agent`
container cannot currently provide directly, or for integration bugs that need
a removable local workaround. The intended deployment model is to mount these
files into Hermes data directories and enable them through Hermes config, not to
edit container-internal source files.

## Layout

```text
plugins/
  qqbot-connect-hotfix/              QQ Bot adapter compatibility plugin
  whatsapp-bridge-policy-hotfix/     WhatsApp bridge/policy/admin plugin
mcp/
  http-gateway/                      Streamable HTTP MCP wrapper and tests
  servers/                           Codex/Hermes MCP client examples
deploy/
  mwe-support-dev/                   Gateway + HTTP MCP + Caddy example
docs/
  architecture.md                    Compatibility design notes
  operations.md                      Install, verify, rollback notes
```

## Current Scope

- QQ Bot: message send fallbacks, media send compatibility, group message
  routing/context buffering, emoji-only mention handling, channel directory
  routing, and long-context compaction support.
- WhatsApp: split private-chat allowlist from group openness, expose group/admin
  controls to Dashboard, patch group bridge intake so group messages are not
  filtered by DM allowlists, and expose `WHATSAPP_REQUIRE_MENTION`.
- MCP: authenticated streamable HTTP MCP wrapper for Hermes, local reverse proxy
  examples, and an optional QQBot target compatibility patch for MCP outbound
  dispatch.

## Installation Shape

Copy plugins into the target Hermes data directory:

```bash
scripts/install-plugins.sh "$HOME/.hermes"
```

Then enable the required plugins from inside the Hermes container or host
runtime:

```bash
hermes plugins enable qqbot-connect-hotfix
hermes plugins enable whatsapp-bridge-policy-hotfix
```

Restart the gateway after enabling or updating plugins.

## Security Boundary

This repository intentionally contains only source, templates, examples, and
tests. Runtime secrets belong in the target Hermes `.env` or a secret manager.
Do not commit:

- Cloudflare tunnel tokens
- dashboard/API passwords or bearer tokens
- QQ/WhatsApp production identifiers
- private phone-number allowlists
- local session data under `.hermes`

## Verification

The plugin tests are self-contained:

```bash
python plugins/qqbot-connect-hotfix/test_hotfix.py
python plugins/qqbot-connect-hotfix/test_media_reply.py
python plugins/whatsapp-bridge-policy-hotfix/test_hotfix.py
python mcp/http-gateway/test_hermes_mcp_http_auth.py
python mcp/http-gateway/test_hermes_mcp_qqbot_target_patch.py
```

For live MCP validation, start the HTTP MCP wrapper and run:

```bash
MCP_URL=http://127.0.0.1:8765/mcp \
MCP_BEARER_TOKEN='<token>' \
python mcp/http-gateway/test_mcp_streamable.py
```
