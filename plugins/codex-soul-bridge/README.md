# Gateway-scoped Codex SOUL bridge / Hermes 专属身份自动注入

This opt-in plugin addresses Hermes `29112bef09`: its regular prompt builder
reads profile SOUL.md, but its Codex app-server path starts/resumes a thread without
forwarding that identity. The local phase plugin preserves sessions/cwd/model
settings but does not forward SOUL either. A file-loader check alone is insufficient.

本插件只在 Hermes Gateway 的实际 `run_codex_app_server_turn` 调用期间读取
所属 profile 的 SOUL。新线程使用 `developerInstructions`；已有线程在下一轮前
通过 `thread/inject_items` 追加 developer 消息。
这里使用的是开发者指令字段，不替换 Codex 的原生 `baseInstructions`。
不写共享 Codex 配置、AGENTS.md、SOUL 副本、全局环境变量或 Codex 二进制。

## Scope and behavior / 作用范围

- A valid Hermes Gateway session key and session ID are required. Profile home
  comes from Hermes' own profile ContextVar/session database resolver, with no
  ambient-home fallback. Merely setting `HERMES_HOME`, sharing `CODEX_HOME`, using
  a Hermes project cwd, or loading the plugin in a hermes-tools MCP process does
  not activate identity injection.
- Use the native `load_soul_md` scanner/size policy; never bypass it with a raw read.
- A ContextVar carries the content during the synchronous runtime call and is
  reset in `finally`. Parallel profiles, nested unscoped calls and exceptions
  cannot retain another call's identity.
- Append on `thread/start`. Preserve explicit developer
  instructions, request config overrides, or effective cwd-resolved existing
  developer instructions from `config/read`. Keep model, sandbox, approval and
  user-turn fields unchanged.
- Before `turn/start` on an existing loaded thread, append a developer SOUL item
  through the owning client only when that thread has not received the current
  content. Cache the successfully applied content per client/thread; unchanged
  content is not repeated. File changes are read on the next Hermes runtime call.
  Successful thread/start overrides seed this cache, avoiding a duplicate.
  A resume response is not proof that new developer instructions reached an old
  thread: CLI 0.159.2 can ignore that override. A newly connected owning client
  therefore injects once before the resumed thread's first turn, then deduplicates.
  Injection failure stops the new turn; it is not marked as applied.
- In-flight turns are not interrupted. Changes take effect at the next turn
  boundary. Removing the file does not erase previously persisted thread history.
- Instructions intentionally belong to the Hermes thread. Manually opening that
  same thread in the App retains its Hermes history/identity. The isolation claim
  covers ordinary independent threads, not erasing content from a Hermes thread.
- Current scope is Gateway sessions (A2A, QQ, WhatsApp, etc.); plain Hermes TUI/cron
  without Gateway identity is excluded. Plain Codex CLI/App is always excluded.

## Validation / 验证记录

[Initial ablation](../../docs/validation/codex-identity-ablation-2026-10-20.md)
compared no injection, fixed identity, SOUL and both. Default/product completed the
matrix; procurement was partly provider-rate-limited. The subsequent
[existing-thread experiment](../../docs/validation/codex-existing-soul-2026-10-20.md)
passed on all three profiles. Version 1.0.1 adds automatic existing-thread refresh
and deduplication; see the [production rollout](../../docs/validation/codex-soul-production-2026-10-20.md). The installer remains
opt-in and does not enable this plugin globally.

## Opt-in installation / 后续启用方式

As the profile's OS owner, back up config.yaml and install explicitly:

```bash
scripts/install-plugins.sh /absolute/hermes/profile codex-soul-bridge
```

Add `codex-soul-bridge` to that profile's existing `plugins.enabled` list. Its
`requires_plugins` entry orders it after `codex-app-server-phase-hotfix`. No Codex
TOML key or auth change is needed. Refresh idle Gateways and their clients through
the normal drain/restart path, then verify real fresh and resumed Hermes turns.
Do not enable it globally in the installer or use Codex's shared instructions file.

## Regression / 回归

```bash
python plugins/codex-soul-bridge/test_bridge.py
HERMES_SOURCE_DIR=/absolute/hermes/source \
CODEX_BINARY=/absolute/codex \
CODEX_APP_BINARY=/absolute/app/bundled/codex \
  /absolute/hermes/venv/bin/python plugins/codex-soul-bridge/test_wire.py -v
```

Nine unit tests cover scope, instruction composition, unknown profile/missing SOUL,
unchanged turn payloads, exception cleanup, nested calls, concurrent profiles,
existing-thread updates/deduplication and fail-closed injection errors.
Seven wire tests use real Codex binaries and a local Responses fixture, with no
online model or credentials: actual Hermes runtime/registration, scoped start and
resume followed by an ordinary new thread in the same process, plain CLI with
Hermes environment/cwd/MCP present, the App-bundled app-server, and existing-thread
injection/history/isolation checks on both binaries, plus a legacy thread that
starts without SOUL and is later resumed with the bridge. Assertions check
the actual outgoing model payload and unchanged configuration bytes. The App
backend test is not an automated desktop UI click-through test.

## Existing-thread experiment / 已有线程追加实验

See the [three-profile live validation](../../docs/validation/codex-existing-soul-2026-10-20.md).

`thread/inject_items` accepts a developer message containing SOUL for a specific
already-loaded, idle thread. It does not start a model turn, restart the client or
create a new thread. The next model request sees the appended identity; the history
item persists across reconnect/resume of the same thread ID. Tests also verify
that another already-loaded ordinary thread and a later new thread remain clean.

Version 1.0.1 uses this path automatically before a Hermes `turn/start` when the
owning client has not applied the current SOUL to that thread. The current runtime
scope and thread ID determine the target; identical content is deduplicated.
Do not open a competing writer for a live Gateway thread. In-flight generation and
post-compaction retention are outside this experiment's acceptance scope.

## Rollback / 回滚

Remove only this plugin from the affected profiles' enabled list, refresh idle
Gateway clients, and verify new ordinary/Hermes threads as appropriate. Remove the
plugin directory after verification. Keep SOUL, existing thread history, auth and
all other plugins intact. Prior Hermes threads can retain their previously saved
instructions/history; removing the plugin does not scrub those threads.

References: [OpenAI App Server documentation](https://learn.chatgpt.com/docs/app-server)
and the schemas generated by each installed binary via
`codex app-server generate-json-schema`. The tested paths are thread-level `developerInstructions` for new threads and
`thread/inject_items` for an existing thread, not shared global instructions.

Remove this compatibility plugin once upstream forwards the correct profile SOUL
for both new and legacy threads and passes the same isolation/continuity checks.
