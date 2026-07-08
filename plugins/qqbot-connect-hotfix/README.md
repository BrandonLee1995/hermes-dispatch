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
QQBot connect signature compatibility, channel-directory chat routing,
`GROUP_MESSAGE_CREATE` context buffering, deterministic group-context compaction,
emoji-only group mentions, reply `msg_id` handling, markdown fallback, and media
caption compatibility.

Compatibility contract:

- QQ official group event payloads expose `id`, `content`, `group_openid`, and
  `author.member_openid` on both `GROUP_AT_MESSAGE_CREATE` and
  `GROUP_MESSAGE_CREATE`, so non-mention group messages can be buffered as
  context while only mention messages are routed to the agent.
- QQ group passive replies must carry a valid recent `msg_id`. This plugin does
  not reuse stale `_last_msg_id` values; explicit `reply_to` is preserved.

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
