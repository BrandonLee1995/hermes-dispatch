# Codex App-Server Compatibility Hotfix

Persistent Hermes plugin for four Hermes 0.18.2 Codex app-server integration
gaps:

1. A completed Codex `final_answer` is also emitted through the gateway
   interim-message callback, which duplicates the final channel reply.
2. Codex's built-in image generator completes as an `imageGeneration` item
   containing base64 bytes. Hermes treats that item as an opaque, truncated
   assistant note. When the turn has no text final answer, the gateway returns
   before its normal media-result scan, so the generated image is never sent.
3. Codex app-server approval requests are wired only to the interactive CLI
   callback. Gateway sessions have no callback, so command/file requests fail
   closed without reaching QQ. Hermes also unconditionally declines
   `item/permissions/requestApproval` with the legacy `decision` response.
   Codex 0.144.6 expects a granted `permissions` subset and optional `scope`.
4. Bundled Computer Use asks for per-app authorization through
   `mcpServer/elicitation/request` with `connector_id=computer-use`. Hermes
   accepts only `hermes-tools` elicitations and silently declines every other
   server, so CLI-hosted Computer Use reports `was not approved` without
   presenting the Gateway user with an approval request.

The plugin forwards explicit `commentary`, suppresses explicit `final_answer`,
and defers unknown-phase messages until later turn activity proves they are
interim. The Codex session projector remains authoritative for final text.

For completed `imageGeneration` items, the plugin validates and atomically
writes the image to:

```text
$HERMES_HOME/cache/images/codex_<item-id>.<ext>
```

It then projects a standard `image_generate` tool result. If the Codex turn has
no final text, it returns `MEDIA:<path>` so the existing Hermes adapter sends
the file as a native image. Set `HERMES_CODEX_IMAGE_CACHE_DIR` only when the
gateway's allowed media roots include that directory. Encoded results above
80 MiB and unsupported file signatures are rejected.

## Gateway approval bridge

The plugin supplies a gateway-aware approval callback only when Hermes did not
already provide one. It looks up the notifier registered for the active Hermes
session, enters the same blocking approval queue used by Hermes terminal tools,
and lets the adapter render its normal approval UI. QQ therefore uses its
existing approval keyboard and `/approve` or `/deny` fallback rather than a
plugin-specific state store.

The bridge covers:

- `item/commandExecution/requestApproval`
- `item/fileChange/requestApproval`
- `item/permissions/requestApproval`
- Computer Use `mcpServer/elicitation/request`

For permission requests, allowing once returns the requested, schema-filtered
permission subset with `scope: turn`. QQ's **始终允许** choice maps only to
`scope: session`; it does not modify `~/.codex/config.toml` or Hermes' permanent
allowlist. Deny, timeout, a missing Gateway notifier, or an internal exception
returns an empty permission profile. Codex treats every omitted permission as
denied.

For Computer Use, the bridge matches only the bundled connector identity,
renders the requested display name and bundle id through the same Gateway
queue, and returns the MCP elicitation `accept`/`decline` response. **始终允许**
adds `_meta.persist=always`, allowing Codex to persist its own Computer Use app
policy. Unknown or third-party MCP elicitations continue through Hermes'
upstream fail-closed handler. Apps marked `forbidden` by the Computer Use
runtime never emit this elicitation and remain blocked.

This behavior follows the current upstream [Codex app-server permission
contract](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md#permission-requests).

Install under the Hermes data directory and enable it:

```bash
scripts/install-plugins.sh "$HOME/.hermes"
hermes plugins enable codex-app-server-phase-hotfix
```

Restart the gateway after installation. Verify all fixes with:

```bash
python plugins/codex-app-server-phase-hotfix/test_hotfix.py
```

To roll back, disable the plugin and restart Hermes. Existing generated cache
files are not deleted automatically:

```bash
hermes plugins disable codex-app-server-phase-hotfix
hermes gateway restart
```

The phase portion behaviorally detects an equivalent upstream fix and becomes a
no-op. The approval portion independently skips its permission or Computer Use
handler after upstream behaviorally implements the corresponding response.
Remove the image portion
once upstream projects `imageGeneration` into a normal deliverable media result
and handles media-only turns before its empty response return. Remove the
approval bridge once Hermes passes Gateway approval callbacks into Codex, uses
the current permission response schema, and forwards Computer Use elicitations.
