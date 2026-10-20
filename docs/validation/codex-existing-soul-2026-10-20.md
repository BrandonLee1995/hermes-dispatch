# Existing-thread SOUL injection / 已有会话追加身份 — 2026-10-20

## Result / 结果

Verified existing-thread acceptance: 3/3 profiles.
无需新建对话：向已加载且当前空闲的原线程追加 SOUL developer 消息，下一轮即可使用。
此实验验证协议路径；生产 Gateway 及候选插件的自动注入逻辑尚未变更。

| Profile | Before injection | After injection | After reconnect | Old context |
|---|---|---|---|---|
| default | unknown / unknown | Pass | Pass | Nonce retained |
| product | product / unknown | Pass | Pass | Nonce retained |
| procurement | procurement / procurement | Pass | Pass | Nonce retained |

Before injection, default reported unknown identity; product lacked its peer alias;
procurement still routed product support to the old default alias. The post-injection
check requires correct internal ID + public alias, all three routing decisions,
the earlier random nonce, no tool calls, and no automatic authority from the role.

## Method / 方法

Reused three pre-existing baseline threads from the earlier ablation (each already
had two turns). These were experiment-owned, previously archived threads, not live
business conversations. Unarchive/resume the same ID, add a fresh random nonce in a
baseline turn, then call the following while the thread is idle:

```json
{
  "method": "thread/inject_items",
  "params": {
    "threadId": "<existing thread id>",
    "items": [{
      "type": "message",
      "role": "developer",
      "content": [{"type": "input_text", "text": "<profile SOUL.md content>"}]
    }]
  }
}
```

The next user message requests identity/routing and the earlier nonce without
repeating the correct identity or nonce. Then close the connection and resume
the same thread ID without any new SOUL override; repeat the check. The original
thread IDs remain unchanged throughout, and the client PID does not change at
injection time. This live script performs no thread/start request.

Default/product use gpt-6.1-sol/high; procurement uses gpt-5.6-sol/high. Models,
credentials and production configuration are unchanged. Procurement requests are
paced at 75 seconds before each model turn because of earlier provider 429s;
this is a temporary test interval, not a deployment protection setting.

## Wire regression / 请求级回归

Two added tests use actual CLI 0.159.2 and App-bundled backend 0.163.0-alpha.10.2
against a local Responses fixture. They verify:

- The injection RPC itself starts no model turn and preserves the existing client.
- The target thread’s next actual outgoing model payload contains SOUL, earlier
  context and the pre-existing developer instructions.
- Reconnect/resume of the same ID retains SOUL and the earlier context.
- A different ordinary thread already loaded before injection remains clean.
- A later ordinary new thread remains clean, even with the same cwd and Codex home.
- Shared config bytes do not change.

The full suite now contains five unit tests and six real-binary fixture tests.
This covers the App backend; no desktop GUI click-through test is claimed.

## Scope and implementation boundary / 边界

Use the original owning Gateway client at a completed-turn boundary. Opening a
competing writer against a live Gateway thread is not this tested method. An
already-dispatched in-flight generation is not retroactively changed; acceptance
is for the next turn after injection. Long histories and compaction retention are
not tested by these short continuations.

The current codex-soul-bridge candidate still appends on thread/start/resume only.
This experiment does not yet add automatic refresh to already-loaded threads.
Such integration should be Gateway-scoped, target only the bound thread, and
deduplicate unchanged SOUL content. Ordinary independent Codex threads must not
receive it. A manually reopened Hermes thread intentionally keeps its own history.

## Evidence / 证据

Private evidence: `~/.hermes/backups/codex-existing-soul-20261020T072711Z`.
Complete replies, original thread IDs, client PID comparisons, persisted developer
items, nonce checks and source-file integrity checks are retained there. Each test
thread is returned to its prior archived state. No business thread is modified.

The earlier ablation audit is bounded to each original attempt’s recorded turn count
so continuing these existing threads does not retroactively reclassify its baseline.
Original history remains intact. No shared config/SOUL/auth file or production plugin
was changed; no QQ/WhatsApp messages were sent.

Reference: [OpenAI App Server — inject items](https://learn.chatgpt.com/docs/app-server#inject-items-into-a-thread).
