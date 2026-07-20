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

## Codex App-Server Image Delivery

`codex-app-server-phase-hotfix` converts completed Codex app-server
`imageGeneration` items into image files below `$HERMES_HOME/cache/images` and
standard `image_generate`/`MEDIA:` output. This compensates for Hermes 0.18.2
truncating the opaque base64 item and dropping media-only turns at its
empty-response branch. The existing QQ adapter then performs native image
upload; no container source file is modified.

After updating the plugin, restart the gateway and ask Codex to generate one
image. Verification succeeds when the gateway log reports the materialized
cache path and QQ receives the image rather than an empty response.

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
