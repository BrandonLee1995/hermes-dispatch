# Development Log

## 2026-07-20 — Computer Use elicitation and QQ shared-group approvals

### Problem

Codex 0.144.6's bundled Computer Use runtime requests access to an unapproved
macOS app through `mcpServer/elicitation/request`, with extended metadata
identifying `connector_id=computer-use`, the bundle id, display name, risk, and
allowed persistence scopes. Hermes 0.18.2 accepts only `hermes-tools`
elicitations and immediately declines every other server. The resulting tool
output is `Computer Use was not approved to use <app>`, but no approval reaches
QQ.

A second upstream mismatch prevents existing QQ buttons from working in shared
groups. With `group_sessions_per_user: false`, the session key has no user id;
the QQ click validator nevertheless requires the operator to match a user id
parsed from that key. Logs confirmed that the initiating member's own click was
rejected as unauthorized.

The request/response shape was checked against Codex's generated 0.144.6
app-server schema, the bundled `@oai/sky` Computer Use policy implementation,
and the upstream [Codex app-server protocol](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md#mcp-elicitation).

### Change

- `codex-app-server-phase-hotfix` 1.3.0 recognizes only bundled Computer Use
  elicitations and bridges them through the existing Gateway queue.
- Allow/deny responses use the current MCP elicitation schema; the persistent
  choice returns `_meta.persist=always` to Codex.
- Third-party elicitations and hard Computer Use safety denials remain
  fail-closed.
- `qqbot-connect-hotfix` 1.5.4 replaces a shared-group approval button's public
  session key with a five-minute, single-use random token bound to the current
  `HERMES_SESSION_USER_ID` and group.
- The token mapping is memory-only, is consumed before queue resolution, and
  cannot approve a later request after a duplicate click.
- QQ `/approve` and `/deny` commands use the same requester record, preventing
  the text fallback from bypassing button ownership.

### Verification

The regression suites cover response mapping, connector filtering, behavioral
upstream detection, actual Hermes approval queue integration, requester/group
matching, unauthorized clicks, and nonce replay:

```bash
python plugins/codex-app-server-phase-hotfix/test_hotfix.py
python plugins/qqbot-connect-hotfix/test_hotfix.py
```

Both versions were copied to the persistent `~/.hermes/plugins` installation
and the launchd-supervised Gateway was restarted. Startup logs confirmed
`Computer Use elicitations patched`, `approval sender patched`, and
`approval dispatcher patched`; QQ reconnected and reached ready state. The two
installed-plugin regression scripts also passed after restart.

End-to-end human acceptance passed on 2026-07-20. The test removed Notes from
the Codex Computer Use allowlist, requested a Notes action from QQ, exercised
the shared-group approval path, approved as the requester, and confirmed that
the interrupted Computer Use call resumed.

### Rollback

Disable both compatibility plugins and restart the Gateway. Codex settings are
not edited by plugin installation; only an explicit **始终允许** response can ask
Codex to persist a Computer Use app policy.

## 2026-07-20 — Codex Gateway approval bridge

### Problem

Codex app-server permission prompts did not reach QQ. Hermes 0.18.2 obtains an
approval callback from its interactive terminal path, but Gateway/cron sessions
have no terminal UI. Command execution and file changes therefore failed closed
without notifying the user. The standalone permissions branch also returned a
hard-coded legacy `{"decision":"decline"}` response.

The active Codex CLI is 0.144.6. Its app-server protocol requires
`item/permissions/requestApproval` responses to contain the granted permission
subset in `permissions`; an optional `scope` selects turn or session lifetime.
Permissions omitted from the response are denied. Upstream reference:
[Codex app-server permission requests](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md#permission-requests).

### Change

Added `codex-app-server-phase-hotfix` 1.2.0 with `approval_bridge.py`:

- injects a Gateway-aware callback when Hermes provides no CLI callback;
- reuses the active session's existing Hermes blocking approval queue;
- surfaces command, file-change, and permission requests through the adapter's
  normal approval UI, including QQ buttons and text fallback;
- returns only schema-approved requested permission fields;
- maps one-shot approval to turn scope and QQ's persistent choice to the current
  Codex session only;
- treats deny, timeout, missing notifier, and bridge errors as no grant;
- detects an equivalent upstream permission-profile response and skips that
  portion of the patch.

No running-container source was edited. Installation remains a copy into the
host-mounted Hermes plugin directory followed by a Gateway restart.

The local deployment copied version 1.2.0 into the persistent Hermes plugin
directory and restarted the launchd-supervised Gateway. Startup logging
confirmed both `gateway callback patched` and `permission requests patched`.

### Verification

The plugin regression script validates phase routing, image materialization,
empty-image final recovery, Gateway callback queueing, requested-subset grants,
an actual Hermes queue round trip, session-scope mapping, and empty-subset
denial:

```bash
python plugins/codex-app-server-phase-hotfix/test_hotfix.py
```

Live verification requires starting a Codex turn through QQ that requests
network or additional filesystem access, confirming that the approval message
arrives, and separately testing allow-once and deny. Silence must time out to an
empty grant.

### Rollback

Disable `codex-app-server-phase-hotfix` and restart the Gateway. This restores
the upstream Hermes 0.18.2 approval behavior and leaves Codex configuration and
permission profiles unchanged.
