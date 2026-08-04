# WhatsApp Bridge Policy Hotfix

Local Hermes WhatsApp bridge compatibility hotfix.

Version 0.2.2 supports both a normal macOS installation and a mounted,
persistent Linux/Docker deployment. Install it under the active Hermes home:

```text
$HERMES_HOME/plugins/whatsapp-bridge-policy-hotfix
```

This resolves to `~/.hermes/plugins/...` on a default macOS installation and
`/opt/data/plugins/...` when `/opt/data` is the container's `HERMES_HOME`.

It avoids editing the running install tree as the durable state. On gateway
startup it asks the active Hermes runtime to resolve its current
`scripts/whatsapp-bridge` directory, copies those files into `runtime/` below
this plugin, patches only `bridge.js`, and points `WhatsAppAdapter` at that
runtime script. Hermes 0.18-era and 0.20 policy-gate source shapes are both
recognized. Hermes 0.20's deferred platform loader is resolved and its live
adapter factory is wrapped, so generating the patched file cannot silently
leave the Gateway using the original bridge.

Compatibility behavior:

- `WHATSAPP_ALLOWED_USERS` remains a DM sender allowlist.
- Group messages are no longer filtered by `WHATSAPP_ALLOWED_USERS` in the Node
  bridge.
- Group access is delegated to the Python adapter's `group_policy` and
  `group_allow_from` / `WHATSAPP_GROUP_ALLOWED_USERS`.
- Slash-command admin gates can be configured from dashboard-visible env vars:
  `WHATSAPP_ALLOW_ADMIN_FROM`, `WHATSAPP_USER_ALLOWED_COMMANDS`,
  `WHATSAPP_GROUP_ALLOW_ADMIN_FROM`, and
  `WHATSAPP_GROUP_USER_ALLOWED_COMMANDS`.
- Hermes 0.20's misplaced
  `display.platforms.whatsapp.require_mention` Dashboard value is honored as a
  compatibility fallback when neither the adapter config nor
  `WHATSAPP_REQUIRE_MENTION` supplies an authoritative value.

The desired model is:

```text
DM:
  sender must satisfy WHATSAPP_ALLOWED_USERS / allow_from

Group:
  WHATSAPP_GROUP_POLICY=open allows joined groups without per-member allowlisting
  sender does not need to be listed in WHATSAPP_ALLOWED_USERS

Admin slash commands:
  controlled separately by DM/group admin allowlists
```

For the common "DM allowlist, group open" model, set:

```text
WHATSAPP_DM_POLICY=allowlist
WHATSAPP_ALLOWED_USERS=<department-private-chat-users>
WHATSAPP_GROUP_POLICY=open
WHATSAPP_ALLOW_ALL_USERS=true
WHATSAPP_REQUIRE_MENTION=true
```

`WHATSAPP_ALLOW_ALL_USERS=true` is required by Hermes' startup guard whenever a
platform policy is `open`. With `WHATSAPP_DM_POLICY=allowlist`, it does not make
private chats open; private-chat intake still requires `WHATSAPP_ALLOWED_USERS`.

The canonical YAML form is:

```yaml
platforms:
  whatsapp:
    require_mention: true
```

Do not place the routing policy under `display.platforms`; that subtree is for
presentation behavior. The compatibility fallback exists for Hermes 0.20
Dashboard output and can be removed after upstream writes the canonical key.

## Path resolution

No path override is normally needed. Resolution order is:

1. `WHATSAPP_BRIDGE_HOTFIX_SOURCE_DIR`, when explicitly configured.
2. Hermes' native `resolve_whatsapp_bridge_dir()` result.
3. `$HERMES_HOME/hermes-agent/scripts/whatsapp-bridge`, then
   `$HERMES_HOME/scripts/whatsapp-bridge`, then the legacy `/opt/hermes` path.

The patched runtime defaults to `runtime/whatsapp-bridge` below the installed
plugin. `WHATSAPP_BRIDGE_HOTFIX_RUNTIME_DIR` and
`WHATSAPP_BRIDGE_HOTFIX_DATA_BRIDGE_DIR` are optional diagnostic overrides.

## Enable and verify

From this repository:

```bash
scripts/install-plugins.sh "$HOME/.hermes" whatsapp-bridge-policy-hotfix
hermes plugins enable whatsapp-bridge-policy-hotfix
hermes gateway restart
```

For Docker, replace `$HOME/.hermes` with the host directory mounted as
`/opt/data`. The Gateway log must report both `runtime bridge installed` and
`adapter factory redirected` with a path below the installed plugin. The live
Node process command must use that same `runtime/whatsapp-bridge/bridge.js`.
Run the regression with the active Hermes Python:

```bash
"$HERMES_HOME/hermes-agent/venv/bin/python" \
  plugins/whatsapp-bridge-policy-hotfix/test_hotfix.py
```

Verify a DM sender outside `WHATSAPP_ALLOWED_USERS` is rejected, an allowed DM
is accepted, and a group member outside that DM list can trigger the agent when
`WHATSAPP_GROUP_POLICY=open` and the mention policy permits the message. With
`WHATSAPP_REQUIRE_MENTION=true`, an ordinary group message must remain silent;
an explicit native mention, reply-to-bot, slash command, or configured mention
pattern may still trigger the agent.

## Rollback

```bash
hermes plugins disable whatsapp-bridge-policy-hotfix
hermes gateway restart
```

The adapter then returns to Hermes' native bridge path. Remove the plugin's
`runtime/` directory only after the Gateway has stopped if disk cleanup is
needed.

Remove this plugin after upstream Hermes implements the same policy split.
