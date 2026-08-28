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
handling, native C2C streaming, bounded input notifications, markdown fallback,
and media caption compatibility.

Version 1.8.13 serializes ordinary final ownership for each QQ private chat and
inbound reply anchor. The adapter registers a short-lived delivery claim before
awaiting the external QQ send, so concurrent `notify=True` callbacks cannot
both deliver the same cancellation or final-only-pending payload. A waiting
callback rechecks the lifecycle state after acquiring the claim: it suppresses
a replay after success, or retries the unchanged tombstone/pending record after
failure. Claims are reference-counted and removed when their last caller exits,
so unrelated chats and anchors remain independent and no adapter-lifetime lock
registry accumulates. Public concurrent regressions cover successful
three-caller cancellation and pending delivery, failure handoff, cancelled
waiter cleanup, cancelled-holder and raised-exception handoff, independent
anchors reaching the external boundary in parallel, an empty claim registry
after exit, and replay suppression.
Install with `scripts/install-plugins.sh`, run the complete Python command in
the Verification section plus `scripts/test_install_plugins.sh`, then restart
only the affected profile. Roll back only from the exact external backup
printed by a successful earlier install.

Version 1.8.12 separates cancellation evidence from successful final delivery.
When a capacity-triggered final-only turn is abandoned, its cancellation
tombstone still suppresses a stale `send_draft`, but it is not eligible to
suppress a later normal turn-final send. The first successful normal final
promotes that record to delivered ownership, so any repeated final is then
acknowledged without another QQ message.

Native-lane membership is now a 1024-chat least-recently-used registry instead
of an adapter-lifetime set. Inactive chats expire first; a chat with an open
native stream remains protected until the stream is sealed or abandoned, and a
live streaming disable still revokes that chat immediately. The installer now
preflights every requested plugin's canonical active target before any plugin
is created, backed up, cleared, or copied. A two-plugin regression proves that
an invalid second target leaves the first directory, including hidden files,
unchanged and creates no backup. Install with `scripts/install-plugins.sh`, run
the complete Python command in the Verification section plus
`scripts/test_install_plugins.sh`, then restart only the affected profile. Roll
back only from the exact external backup printed by a successful earlier
install.

Version 1.8.11 makes the public adapter identity boundary explicit. Active
native streams are keyed by `(chat_id, draft_id)`, so two private chats may use
the same Hermes draft id concurrently without sharing or rejecting state. A
capacity-triggered final-only turn that is successfully abandoned now records
a completed owner before its pending identity is removed, preventing a late
draft callback from reopening that cancelled turn after capacity is freed.

The completed-owner and final-only-pending registries retain their independent
256-entry per-chat quotas and now also cap their least-recently-used outer chat
sets at 1024. Recent activity moves a chat to the end of its own registry; when
the total-chat bound is exceeded, only the least-recently-used chat bucket in
that registry expires. This bounds adapter memory while preserving independent
quotas for recently active conversations. The installer also validates the
profile-level backup root before it creates an absent active plugin directory,
so a rejected fresh install leaves no empty `plugins/<name>` artifact. Public
adapter and installer regressions cover each behavior. Roll back only from the
exact external backup printed by the installer.

Version 1.8.10 isolates completed-turn ownership per private chat. Each chat has
its own 256-entry FIFO quota, so high completion volume in one QQ conversation
cannot evict another conversation's replay protection. Tombstones are now
created for every successful managed completion path: ordinary suffix fallback,
all-native seal (including rollover and second-round recovery), first-frame
final-only degradation, capacity-triggered final-only degradation, a
committed-only rollover head, and successful draft abandonment. Capacity-only
turns retain a separate per-chat pending identity until their ordinary final
succeeds, allowing the completed owner to keep the original Hermes draft id.
Repeated final callbacks and late draft frames for each path are regression
tested through the public adapter lifecycle.

The installer used by this release validates canonical filesystem boundaries
before any backup or replacement. A profile-level `plugin-backups` symlink is
rejected even on a first install; an active `plugins/<name>` symlink is rejected
for both install and restore; the canonical active target must be one direct
child of the canonical plugin root; and `.`/`..` are not valid plugin names.
These checks prevent an old manifest from escaping into recursive discovery,
prevent writes through an external active-target link, and prevent restore from
clearing the whole plugin root. `scripts/test_install_plugins.sh` covers normal
canonical install/restore plus every rejected boundary before active data is
changed. Roll back with an exact installer-created external backup as described
below.

Version 1.8.9 keeps final ownership after a successful native recovery close.
When an ordinary fallback has already delivered the unseen final suffix, the
adapter records a completed-turn tombstone keyed by private chat, inbound reply
anchor, and Hermes draft id before the active stream is removed. The map is
bounded to 256 recent turns. A repeated turn-final callback or late draft frame
for that exact completed turn is acknowledged without creating another QQ
message, while a different inbound anchor remains a distinct new turn even if
the draft id is reused. This compensates for Hermes consumer cleanup callbacks
that can arrive after QQ has accepted the recovery seal.

The terminal ownership boundary now uses Unicode punctuation categories plus
whitespace rather than a handwritten punctuation list. ASCII/CJK commas, em
dashes, and other Unicode punctuation therefore suppress an already-streamed
completed commentary carrier consistently, while word-internal suffixes remain
unowned. `test_streaming.py` covers successful recovery removal, repeated
finals, late frames, anchor isolation, bounded eviction, Unicode interim
carriers, and Unicode turn-final composition. Install with
`scripts/install-plugins.sh`; it now creates a timestamped external backup under
`$HERMES_HOME/plugin-backups` before replacement. Restore that exact backup
with the documented `--restore` command and restart only the affected profile.

Version 1.8.8 removes a second message-carrier race found by the real QQ C2C
canary. Codex app-server can stream a commentary item's live deltas and then
Hermes can emit the completed item again as an ordinary `_interim_send` without
its original reply anchor. When the same anchored open native stream already
owns that exact token-bounded terminal payload, the adapter now acknowledges
the interim callback without posting a duplicate ordinary QQ bubble. If Hermes
does not provide an anchor, the plugin recovers only a unique matching open
stream in the same private chat; multiple concurrent matches remain ambiguous
and keep the ordinary path. Earlier/nonterminal occurrences, word-internal
suffixes, unopened streams, other anchors, groups, and non-interim messages are
unchanged. `test_streaming.py` covers the real Gateway consumer sequence,
unique and ambiguous unanchored recovery, rollover-boundary ownership, and all
negative isolation cases. Roll back a deployed copy by restoring its external
pre-install backup; do not assume a version-named artifact exists in Git.

Version 1.8.7 closes the remaining final-ownership gaps found during review of
1.8.6. When an unseen final suffix is successfully delivered by the immutable
ordinary-message fallback but the native-prefix recovery seal remains pending,
the retained stream now records that ordinary owner. A delayed
`abandon_open_draft`, a repeated turn-final callback, or a late draft frame may
close the native prefix but cannot absorb or resend the ordinary-owned suffix.
This prevents a recovered stream from displaying the final twice.

Final composition no longer treats an arbitrary substring or suffix/prefix
overlap as ownership. A cumulative final must explicitly extend the complete
QQ-visible body. An independent final is considered already visible only when
the exact payload is at the terminal position with a token boundary; otherwise
the complete payload is appended once. The composer also respects an existing
leading whitespace boundary instead of inserting a second newline. These rules
compensate for Hermes turns where commentary may mention the same words as the
later independent final. `test_streaming.py` covers delayed close, final retry,
late-frame races, non-terminal repeats, partial overlaps, word-internal suffixes,
leading boundaries, cumulative replacement, and exact final ownership. Roll
back by restoring 1.8.6 from outside the plugin discovery tree and restarting
only the affected profile.

Version 1.8.6 makes final-message ownership explicit across sealed native
chunks, the currently visible native stream, and an ordinary fallback. Hermes
can supply either a cumulative final or a short independent answer; the plugin
now composes both forms losslessly with the QQ-acknowledged draft before one
unified rollover path. A full 4000-character draft therefore rolls an
independent final into a new stream instead of truncating it. If rollover stops
while sealing a head, only the suffix that QQ has never acknowledged is sent
normally. If a tail is already visible but its close fails, the plugin retries
that close without sending the same tail normally; after both bounded close
rounds fail, the visible state remains addressable for a later abandon/retry.
The seal composer also returns overflow explicitly instead of silently capping
it. These guarantees compensate for Hermes' mixed cumulative/final-only turn
payloads and QQ's immutable replace-prefix plus 4000-character stream limit.
No new setting is required: keep the C2C streaming settings below enabled and
run `test_streaming.py`. The regression suite checks exact single ownership for
full-draft independent finals, partial-draft growth, exhausted head-seal
retries, tail-open failure, and tail-close recovery. Roll back by restoring
1.8.5 from outside the plugin discovery tree and restarting only the affected
profile.

Version 1.8.5 extends rollover ownership to the authoritative final payload.
If the visible draft is still below QQ's limit but the final first crosses it,
the active stream is sealed at the limit and a new stream carries the suffix;
the suffix is no longer truncated. If a prior overflow head is already sealed
but QQ cannot open the new tail stream, the ordinary fallback sends only the
uncommitted suffix instead of duplicating the sealed prefix. A successful
suffix fallback also removes the unopened placeholder state. These two paths
are regression-tested with 3900-to-4100 final growth and repeated tail-open
failure. Roll back this release by restoring 1.8.4 from outside the plugin
discovery tree and restarting the affected profile.

Version 1.8.4 gives QQ native streams their own overflow lifecycle. When a
cumulative C2C reply exceeds QQ's per-message limit, the plugin seals the full
active stream chunk and opens a new stream for only the remaining suffix.
Every stream therefore keeps an independent prefix-stable `replace` sequence;
Hermes' generic ordinary-message overflow path is bypassed only for an active
QQ C2C native lane. The turn-final seal covers the last stream, so the sealed
chunks concatenate to the authoritative final response without an ordinary
duplicate.

The 1.8.4 route gate also distinguishes QQ C2C from guild direct messages.
Both arrive with `chat_type="dm"`, but only the adapter's explicit `"c2c"`
route may use `/v2/users/{openid}/stream_messages`. A live configuration
transition from enabled to disabled removes the chat from the native lane on
the next turn; an already-open stream remains addressable through the stream
map until it is sealed or abandoned. `test_streaming.py` covers continued
output beyond one message, final sealing, guild-DM rejection, and the
enabled-to-disabled transition. Roll back this release by restoring 1.8.3
from outside the plugin discovery tree and restarting the affected profile.

Version 1.8.3 activates the native QQ lane only after resolving both Hermes'
global streaming switch and `display.platforms.qqbot.streaming`. Consumer
creation for `interim_assistant_messages=true` is not treated as evidence that
streaming is enabled. This preserves the upstream typing and final-only path
when a profile explicitly opts out of streaming. Version parsing is also
strict: pre-release, local-suffix, and unknown version strings fail closed.

Version 1.8.3 requires Hermes 0.20.5 or newer for native C2C streaming. Hermes
0.20.0 does not pass `chat_id` to the draft-capability probe and its
`GatewayStreamConsumer.finish()` cannot accept the authoritative final text.
On an older or unknown runtime the streaming patch now fails closed: it does
not replace `send`, `send_typing`, or the Gateway stream gate, while the other
QQ hotfix modules continue to load. Check with `hermes --version`; on an older
installation run `hermes update --check`, run `hermes update --plan` only when
`hermes update --help` lists that option, then run `hermes update --backup` and
verify a stable 0.20.5 or newer release before enabling the settings below.

The 1.8.2 typing budget applies only after the Gateway has selected a native
C2C lane for that chat (or a native stream is actually open). With streaming
disabled, the plugin leaves Hermes' original periodic `send_typing` behavior
unchanged. A transient final-seal error is retried at the same unacknowledged
index. If those retries fail, the complete ordinary final is sent first; the
plugin then tries to close the older stream with its last acknowledged partial
body, avoiding a second copy of the final answer. If QQ remains unavailable,
the opened state is retained so `abandon_open_draft` or a later seal attempt can
still close it. Capacity pressure removes only streams whose first frame never
opened; it never discards a client-visible stream. The extra turn stays
final-only when all 128 slots are opened. These disabled-mode, retry, recovery,
safe-degradation, and capacity contracts are covered by `test_streaming.py`
against the official Hermes 0.20.0 and 0.20.5 release sources.

Version 1.8.1 preserves QQ's already-submitted stream prefix when Hermes seals
a tool-using private-chat turn. Hermes' cumulative draft can contain commentary
and tool progress before the final answer, while its turn-final `send()` may
contain only the short final answer. Sending that shorter text with
`input_mode=replace` removes the visible prefix and QQ rejects the
`input_state=10` frame as immutable content. The hotfix now seals with the
cumulative draft when it already contains the final, or appends only the
non-overlapping final suffix when needed. This keeps one visible message,
allows the native stream to reach its completed state, and retains the existing
single ordinary-final fallback if the stream itself fails. Verify with
`test_streaming.py` and a tool-using C2C task whose commentary is visible before
a short final. Roll back by restoring 1.8.0 and restarting only the affected
profile Gateway.

Keep rollback copies outside every configured plugin discovery root. Hermes can
recursively discover a `plugin.yaml` below the profile `plugins` directory, so
a path such as `plugins/.backups/qqbot-connect-hotfix-1.8.0` may register the
old copy before the active plugin. A profile-level path such as
`plugin-backups/qqbot-connect-hotfix-1.8.0-<timestamp>` keeps the backup
available without loading it.

Version 1.8.0 adds QQ's official C2C streaming-message protocol without
modifying the installed Hermes package.  The plugin advertises native draft
streaming only for private chats, maps Hermes cumulative draft frames to
[`POST /v2/users/{openid}/stream_messages`](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_users_user_openid_stream_messages.post.html)
with `input_mode=replace`, and seals
the same visible message with `input_state=10` when the turn completes.  Each
stream is keyed by chat, inbound `msg_id`, and Hermes draft id so concurrent
private sessions cannot share indices or `stream_msg_id` values.  Approval,
slash-command, heartbeat, and steering messages bypass the seal path and remain
independent messages.

Hermes 0.20.5 still rejects adapters with
`SUPPORTS_MESSAGE_EDITING=false` before its stream consumer probes native draft
support. QQ ordinary messages are not editable, but C2C streams do not require
ordinary-message editing. Version 1.8.0 therefore bypasses that legacy gate
only when the active adapter is QQ and the source is a C2C chat. QQ groups and
all other non-editable platforms retain the upstream guard. If a native frame
cannot be opened, the adapter keeps the consumer in a buffered final-only lane
so the user receives one ordinary final instead of an uneditable partial plus
a duplicate final.

QQ counts `input_notify` calls against the passive-reply budget associated with
an inbound message.  Hermes normally refreshes that status every 50 seconds;
long turns can therefore exhaust the budget before their final response.  The
1.8.0 patch permits at most one typing notification per inbound `msg_id` and
then uses the native stream for continuing status.  If the first stream frame
fails, Hermes falls back to its normal final-message path.

Enable the feature per profile:

```bash
hermes --version  # must report 0.20.5 or newer
```

```yaml
streaming:
  enabled: true
  transport: auto

display:
  platforms:
    qqbot:
      streaming: true
      interim_assistant_messages: true
      tool_progress: new
```

Verification:

```bash
PYTHONPATH=/path/to/hermes-agent \
  /path/to/hermes-agent/venv/bin/python \
  plugins/qqbot-connect-hotfix/test_streaming.py
```

In a real QQ private chat, start a tool-using task and verify that one message
updates in place, a completed commentary is not repeated in an ordinary bubble,
the last frame is sealed rather than duplicated, `/steer` does not seal the
stream, and logs contain neither error `40034128` nor a second final send. Roll
back by setting
`display.platforms.qqbot.streaming: false` and restarting only the affected
profile's Gateway. The restart creates a fresh adapter, so the native-lane
typing budget is also removed. To roll back the complete current code change,
restore the previous plugin directory from outside the plugin discovery tree
with `scripts/install-plugins.sh --restore`, then restart that profile.

Version 1.7.0 adds the narrow expired-reply fallback used by upstream Hermes
PR [#85221](https://github.com/NousResearch/hermes-agent/pull/85221). QQ can
reject a valid inbound `msg_id` after a long-running turn. Text sends now keep
the reply anchor on the first attempt and, only when QQ explicitly reports that
`msg_id`/`message_id` expired, retry once as a standalone message. The same
low-level wrapper covers C2C text, group text, approval keyboards, and guild
text while preserving the keyboard payload. It does not change task lifetime,
Codex app-server timeouts, or media delivery; those remain separate concerns.

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
- QQ replies use the explicit inbound `reply_to` while it remains valid and do
  not reuse stale `_last_msg_id` values. If QQ explicitly rejects that anchor as
  expired, version 1.7.0 retries text or keyboard delivery once without the
  reply relationship. Unrelated errors are returned unchanged. Media does not
  use this fallback yet.
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
