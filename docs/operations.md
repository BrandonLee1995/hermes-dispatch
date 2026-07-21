# Operations

## Install Plugins

From the repository root:

```bash
scripts/install-plugins.sh "$HOME/.hermes"
```

Inside a containerized deployment the target path is the host directory mounted
as `/opt/data`.

Enable plugins:

```bash
hermes plugins enable qqbot-connect-hotfix
hermes plugins enable codex-app-server-phase-hotfix
hermes plugins enable message-snapshot-store
hermes plugins enable whatsapp-bridge-policy-hotfix
```

Restart Hermes gateway after enabling or updating plugins.

## Permanent Message Snapshots

`message-snapshot-store` 1.0.1 registers an explicit raw-event hook that
`qqbot-connect-hotfix` 1.5.3 invokes before suppressing passive group routing.
It therefore captures raw QQ events regardless of plugin load order into:

```text
$HERMES_HOME/message-snapshots/snapshots.sqlite3
```

QQ defaults a group robot to mention-only delivery. The group owner can open
the group's robot settings and enable **获取全部群消息**. Once enabled, ordinary
messages arrive as `GROUP_MESSAGE_CREATE` and are captured before routing. If
the setting remains off, unmentioned text and media are outside the observable
event stream and cannot be snapshot. To recover older media in that mode,
reply to/quote it and mention the bot; a `message_type=103` event can carry the
referenced attachment.

The native group-owner setting is the authorization boundary and does not need
to be toggled or reconfirmed when it is already effective. Hermes 0.18.2 also
acknowledges QQ connector configuration interactions without their required
`claw_cfg`; the hotfix handles interaction types 2001/2002 when QQ sends them,
but that separate compatibility exchange is not a prerequisite for native
full-message delivery.

`qqbot-connect-hotfix` 1.5.2 and later also gate QQ's broad
`GROUP_AT_MESSAGE_CREATE` label with `mentions[].is_you`. Messages mentioning
the owner or another member remain snapshot/context input and do not wake the
agent as though the bot itself had been mentioned.

The default `MESSAGE_SNAPSHOT_MEDIA_STORAGE=link` records QQ media URLs and
metadata without copying bytes. `/message-snapshot restore <id>` explicitly downloads
and pins an attachment when the current QQ credentials can still access its
URL. Set `MESSAGE_SNAPSHOT_MEDIA_STORAGE=mirror` only when offline permanence
is worth the storage cost.

Recent group context defaults to 20 messages and approximately 4000 tokens:

```text
MESSAGE_SNAPSHOT_CONTEXT_MESSAGES=20
MESSAGE_SNAPSHOT_CONTEXT_TOKENS=4000
```

## Codex App-Server Compatibility

`codex-app-server-phase-hotfix` converts completed Codex app-server
`imageGeneration` items into image files below `$HERMES_HOME/cache/images` and
standard `image_generate`/`MEDIA:` output. This compensates for Hermes 0.18.2
truncating the opaque base64 item and dropping media-only turns at its
empty-response branch. The existing QQ adapter then performs native image
upload; no container source file is modified.

Version 1.3.0 also bridges Codex command execution, file change, permission,
and bundled Computer Use app-authorization requests into Hermes' registered
Gateway approval queue. This compensates for Hermes 0.18.2 using only the
terminal callback for command/file approvals and hard-coding permission and
non-Hermes MCP elicitation requests to decline. On Codex 0.144.6, permission
approval returns the requested, schema-filtered subset; denial or timeout
returns an empty subset. Computer Use approval returns the MCP elicitation
response and lets Codex own session/permanent app policy.

`qqbot-connect-hotfix` 1.5.4 makes those buttons usable when
`group_sessions_per_user: false`: each prompt carries a short-lived opaque
nonce bound to the member who initiated the current turn. Another member, a
different group, an expired token, or a repeated click cannot resolve the
pending approval. Typed `/approve` and `/deny` commands enforce the same owner
binding.

After updating the plugin, restart the gateway and ask Codex to generate one
image. Verification succeeds when the gateway log reports the materialized
cache path and QQ receives the image rather than an empty response.

To verify approvals, ask Codex through QQ to perform an operation that requires
network access or an additional writable path. The QQ chat must receive an
approval request while the turn remains blocked. **允许一次** grants only that
turn; **始终允许** is limited to the active Codex session; **拒绝**, silence,
send failure, or timeout grants nothing. Do not enable `/yolo` or set
`approvals.mode: off` for this test because those modes intentionally bypass
the human approval boundary.

To verify Computer Use specifically, first remove the test app from Codex App's
Computer Use allowlist, then ask through QQ to operate a non-forbidden app such
as Notes. The QQ approval card must name the app. Verify rejection by another
group member, approval by the requester, successful continuation of the same
turn, and **始终允许** reuse on a later turn. A hard safety-denied app must remain
blocked without a bypass prompt.

Check capture and FTS5 availability with `/message-snapshot stats`. Search examples:

```text
/message-snapshot search 关键词 chat_id=<id>
/message-snapshot search attachment_filename=report.pdf
/message-snapshot search field_path=author.member_openid value=<id>
```

## WhatsApp Group Response Control

Use:

```text
WHATSAPP_REQUIRE_MENTION=true
```

When enabled, group messages are processed only if one of these is true:

- the message mentions the bot
- the message replies to the bot
- the message starts with `/`
- the message matches `WHATSAPP_MENTION_PATTERNS`

Set it to `false` to let all allowed group messages reach the agent.

## Rollback

Disable the plugin and restart the gateway:

```bash
hermes plugins disable whatsapp-bridge-policy-hotfix
hermes plugins disable qqbot-connect-hotfix
hermes plugins disable codex-app-server-phase-hotfix
hermes plugins disable message-snapshot-store
```

Disabling `codex-app-server-phase-hotfix` restores Hermes 0.18.2 behavior:
Codex Gateway approvals are no longer sent to QQ, permission requests fail
closed, Computer Use app elicitations are declined, duplicate-final protection
is removed, and Codex media-only image turns may again be dropped. Disabling
`qqbot-connect-hotfix` restores the shared-group approval rejection when group
sessions are not user-isolated. Restart the gateway after rollback.

For script-level MCP changes, stop the `http-mcp` container or point the compose
service back to the upstream Hermes MCP command.

## Verification

Run plugin tests on the host:

```bash
python plugins/qqbot-connect-hotfix/test_hotfix.py
python plugins/qqbot-connect-hotfix/test_media_reply.py
python plugins/qqbot-connect-hotfix/test_group_roundtrip.py
python plugins/codex-app-server-phase-hotfix/test_hotfix.py
python plugins/message-snapshot-store/test_store.py
python plugins/message-snapshot-store/test_capture.py
python plugins/message-snapshot-store/test_materialize.py
python plugins/message-snapshot-store/test_quoted_attachment.py
python plugins/whatsapp-bridge-policy-hotfix/test_hotfix.py
```

Run inside a Hermes container when validating the real image:

```bash
docker exec <gateway-container> /opt/hermes/.venv/bin/python3 \
  /opt/data/plugins/whatsapp-bridge-policy-hotfix/test_hotfix.py
```

For HTTP MCP:

```bash
python mcp/http-gateway/test_hermes_mcp_http_auth.py
python mcp/http-gateway/test_hermes_mcp_qqbot_target_patch.py
```
