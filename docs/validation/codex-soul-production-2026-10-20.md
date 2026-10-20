# Codex SOUL automatic refresh production rollout / 身份自动补注入生产接入

## Final state / 最终状态

2026-10-20，本机 default、product、procurement 三个 profile 已安装并启用
`codex-soul-bridge` **1.0.1**。本次只增加该插件及其 `plugins.enabled` 项，
内部 profile ID、A2A 别名/地址/鉴权、模型设置和共享 Codex 配置均保持不变。

Each existing production A2A canary conversation passed two consecutive turns:
correct internal ID, public peer alias, all three collaboration routes, retained
pre-deployment nonce, and no authorization implied merely by the role. The exact
Hermes session ID and Codex thread ID remained unchanged. Target transcripts show
one SOUL developer item per thread across both accepted continuations, zero tool
calls, and no terminal runtime errors.

| Internal profile | Peer alias | Accepted continuations | Same session/thread | SOUL developer items |
|---|---|---:|---|---:|
| default | product-support | 2/2 | Yes | 1 |
| product | product | 2/2 | Yes | 1 |
| procurement | procurement | 2/2 | Yes | 1 |

All three final Gateways are running with A2A and QQ connected. Gateways were
refreshed sequentially through the native idle/drain restart path. No `/new`,
session reset or history deletion was used. Tests sent only local configured-peer
A2A requests, not QQ/WhatsApp messages. Pre-existing unrelated WhatsApp connection
errors were not repaired or treated as A2A failures.

## Runtime behavior / 运行方式

- On a genuine Hermes Gateway Codex turn, resolve the agent's own profile home
  and read SOUL through Hermes' native scanner/loader.
- For a new thread, append SOUL to its developer instructions while preserving
  existing instructions and model/permission settings.
- For an existing/resumed thread not yet confirmed by the owning client to have
  the current SOUL, call `thread/inject_items` before its next `turn/start`.
- Cache successfully applied content per live client/thread. Unchanged content
  is not appended on every turn; edited SOUL is applied on the next runtime call.
- A resume response alone does not mark identity as loaded. A new owning client
  may safely reassert SOUL after a reconnect; deduplication is per live client.
- An injection error prevents starting that turn and does not mark it loaded.
- Only the scoped Hermes runtime call activates this behavior. Ordinary Codex
  CLI/App, matching cwd, inherited HERMES_HOME, shared CODEX_HOME, or MCP plugin
  discovery alone do not activate it. No global environment variable is changed.

## Issue found during rollout / 上线时发现并修复的问题

The initial 1.0.0 rollout did **not** pass acceptance: default retained its nonce
and thread ID but still answered unknown identity. Temporary diagnostics confirmed
that the correct SOUL was loaded and the runtime/request ContextVars matched.
The problem was an optimistic cache mark after `thread/resume`.

A new real-binary fixture reproduced this exact case: start a thread without SOUL,
complete a turn, close the client, then resume with `developerInstructions`. On
CLI 0.159.2 the subsequent model input did not contain the new SOUL even though
resume returned successfully. Earlier tests resumed threads that already had
SOUL, so they did not catch this legacy-thread behavior.

1.0.1 no longer relies on resume overrides for identity. It explicitly injects
before the next turn and marks success only after that RPC succeeds. The new
regression failed before the change and passed afterward. Temporary diagnostic
code was removed from every active plugin before final acceptance. Initial failed
results and diagnostic evidence remain in the private backup; they are not counted
as successful deployment checks.

## Regression and integrity / 回归与完整性

- **9 unit tests passed:** profile/runtime scope, instruction preservation,
  unknown home/missing SOUL, exception cleanup, nested and concurrent contexts,
  existing-thread refresh, deduplication, injection failure, and legacy resume.
- **7 real-binary/local-Responses wire tests passed:** actual Hermes runtime with
  an already-started client, live content changes without thread/PID change,
  non-repetition, legacy resume, existing-thread history, and ordinary old/new
  thread isolation. These fixtures use no online model.
- CLI **0.159.2** and desktop-bundled backend **0.163.0-alpha.10.2** were checked.
  App backend payloads were tested directly; no desktop GUI automation is claimed.
- Three-profile live acceptance then passed **6/6** A2A continuations after the fix.
- Protected-file SHA-256 comparisons matched: `.env`, SOUL, profile metadata,
  Hermes/Codex auth, Codex config, existing A2A/phase/directory plugin code and
  existing host scripts. Only the new enablement entry changed YAML semantics.
- Active plugin code/manifests exactly match the tested 1.0.1 repository files.

A random nonce was supplied before deployment. One baseline answer returned
unknown for the nonce, but the original user message persisted it; both accepted
post-deployment replies subsequently returned the correct original value. Nonce
retention is checked against the pre-deployment input, not fabricated from a new
prompt or a reset conversation.

## Backup and rollback / 备份与回滚

Private backup: `~/.hermes/backups/codex-soul-production-20261020T074729Z/`.
It contains before/after YAML, protected-file hashes, pre-deployment canaries,
initial failed checks, final accepted replies, session/thread mappings, diagnostics,
restart snapshots, final verification and the rollout scripts. Runtime backups
contain credentials/identifiers and must not be committed.

Rollback by removing only `codex-soul-bridge` from the three enabled lists, or
restoring the original config backups only if no later unrelated edits would be
lost. Refresh idle Gateway clients, verify readiness, then remove this plugin's
files. Keep all credentials, SOUL and other plugins unchanged. Do not roll back
to 1.0.0 as a fix for the legacy-thread problem; that is the rejected rollout.
Disabling the plugin stops future injection, but does not scrub SOUL already saved
in existing conversation history.

## Limits / 限制

This rollout refreshes identity at turn boundaries. It does not interrupt an
in-flight generation or migrate all inactive histories eagerly; existing business
conversations receive identity when they next run. Actual QQ/WhatsApp message
round trips, long-history compaction retention and remote hosts were not tested.
The procurement acceptance probe used a temporary 75-second test delay; no wait,
serialization or rate-limit setting was added to production.

No commit, push or remote publication was performed during this deployment.
The subsequent [PR release checks](a2a-release-2026-10-20.md) are recorded separately.
