# QQBot Connect Hotfix

Local Hermes QQBot adapter compatibility hotfix.

This plugin is seeded into each department profile as:

```text
/opt/data/plugins/qqbot-connect-hotfix
```

It is enabled by default in `templates/config.yaml`:

```yaml
plugins:
  enabled:
    - qqbot-connect-hotfix
```

Remove this plugin after the upstream Hermes image includes equivalent fixes for
QQBot connect signature compatibility, QQ group configuration interaction ACKs,
channel-directory chat routing,
`GROUP_MESSAGE_CREATE` context buffering, deterministic group-context compaction,
structured self-mention gating, emoji-only group mentions, reply `msg_id`
handling, markdown fallback, and media caption compatibility.

Version 1.6.1 keeps the shared-group approval wrapper compatible with both
Hermes 0.18.2 and the newer 0.19-era cross-adapter contract. New Gateway code
passes an explicit `allow_session` keyword to `send_exec_approval`; the old
wrapper rejected that keyword before QQ could send a keyboard and forced the
Gateway into its plain-text `/approve` fallback. The wrapper now accepts
current and future keyword additions, forwards only parameters implemented by
the installed adapter, and preserves `allow_session` when the adapter supports
it. This is a runtime signature compatibility fix; it does not broaden any
approval scope.

Version 1.6.0 restores the Codex approval choices that Hermes 0.18.2 drops from
QQ. The upstream adapter passes `allow_permanent=False` for command and file
requests, so QQ renders only allow/deny even though app-server supports
`acceptForSession`. The Codex compatibility plugin now records the exact
request-scoped choices on Hermes' existing short-lived approval queue. This
plugin reads that entry and renders:

- **本次允许**
- **会话允许**
- **始终允许同类**, only when Codex proposed a persistent command or network
  policy amendment
- **拒绝**

The buttons use two rows for QQ mobile compatibility. The new
`allow-session` callback maps to Hermes' existing `session` queue decision.
Permission and file-change prompts never claim permanent scope; Computer Use
shows permanent approval only when its elicitation advertises `persist=always`.
If the Codex plugin is disabled or the queue has no decision metadata, the
upstream QQ keyboard remains unchanged.

Version 1.5.4 also compensates for shared-group approval ownership. With
`group_sessions_per_user: false`, Hermes' group session key contains no user id,
but the upstream QQ click validator requires one; consequently every approval
button is rejected, including a click by the person who initiated the turn.
The plugin captures `HERMES_SESSION_USER_ID` when the approval is sent, places a
short-lived opaque nonce in the QQ button, and resolves the real Gateway session
only when the same group member clicks it. The nonce is single-use, expires with
the normal five-minute approval timeout, and is kept only in process memory.
This preserves shared group context without allowing other members or stale
buttons to approve a later request. The same requester check covers typed
`/approve` and `/deny`, so the text fallback cannot bypass button ownership.

Compatibility contract:

- Tencent's current connector answers `INTERACTION_CREATE` configuration query
  and update types `2001`/`2002` with a `claw_cfg` object. Hermes 0.18.2 ACKs
  those events without the required data. This plugin implements the narrow
  ACK contract, including QQ's `claw_type=openclaw` wire identifier, and
  defaults `QQBOT_GROUP_RECEIVE_MODE=all`. The independent
  `QQBOT_GROUP_MESSAGE_CREATE_MODE=mention` default still prevents the agent
  from responding to every passive group message.

- When delivered, QQ group event payloads expose `id`, `content`,
  `group_openid`, and `author.member_openid` on
  `GROUP_AT_MESSAGE_CREATE`/`GROUP_MESSAGE_CREATE`, so the latter can be
  buffered while only mention messages are routed to the agent.
- QQ defaults each group's robot receive scope to mention-only. The **group
  owner** (not an ordinary member or group administrator) can open the QQ group
  robot settings and select **获取全部群消息**. QQ then delivers ordinary group
  traffic as `GROUP_MESSAGE_CREATE` on the existing connection; this plugin
  buffers those events and injects recent context when the bot is mentioned.
  Before returning from a passive event, version 1.5.3 invokes the optional
  `message-snapshot-store` raw-capture hook. This keeps full-message snapshots
  independent of plugin load order without routing passive traffic to the
  agent.
  Without that per-group switch, the server does not deliver unmentioned
  messages and no Hermes-side plugin or database wrapper can recover them.
  The native owner setting itself is authoritative. The separate 2001/2002
  compatibility patch responds with `claw_cfg` only when QQ sends a connector
  configuration interaction; owners do not need to toggle or reconfirm an
  already effective native permission just to activate snapshot capture.
- QQ group passive replies must carry a valid recent `msg_id`. This plugin does
  not reuse stale `_last_msg_id` values; explicit `reply_to` is preserved.
- QQ may label a message that mentions another member as
  `GROUP_AT_MESSAGE_CREATE`. Version 1.5.2 and later check the authoritative
  `mentions[].is_you` field, so @owner/@member traffic is captured as context
  but does not wake the agent; only an actual @bot does.

Group context controls:

```text
QQBOT_GROUP_CONTEXT_MESSAGES=20
QQBOT_GROUP_CONTEXT_BUFFER_MESSAGES=100
QQBOT_GROUP_CONTEXT_CHARS=4000
QQBOT_GROUP_CONTEXT_SUMMARY_CHARS=1200
```

When the buffered group history exceeds the message or character threshold, the
plugin sends a compact extractive block: a count and small sample of earlier
messages plus the latest messages that fit in the budget. It does not call an
LLM for compression.
