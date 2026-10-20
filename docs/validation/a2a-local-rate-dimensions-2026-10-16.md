# 本机多 profile A2A 限流维度实测 / Local A2A rate-limit probe

## 结论 / Findings

2026-10-16 16:55–16:59（Asia/Shanghai），本机三个 profile 完成 **19 次 A2A
接收端任务**，全部返回预期完整标记，目标 Codex turn 无终止错误。本轮没有复现历史
429，因而**不能确定历史 429 的具体限流维度或数值阈值**。

可以确认：三个 profile 使用同一个在线账号、同一个 `codex` 周额度桶；增加 profile
没有产生独立额度。实测同时运行三个 A2A Codex turn 成功，包括同一 profile、同一模型
的三个独立上下文。未观察到固定为 1 或 2 的 A2A 活跃 turn 上限。

English: All 19 receiving A2A tasks passed, including three concurrent turns on
one profile/model and a model-initiated nested A2A call. All profiles reported the
same live account and weekly quota bucket. Historical HTTP 429 was not reproduced;
its exact cause and any RPM, TPM, or concurrent-generation ceiling remain unknown.
These results establish a tested operating point, not a guaranteed service limit.

## Environment / 环境

- Three local native macOS profiles: `default`, `product`, `procurement`.
- Shared Hermes checkout `29112bef09`; Codex CLI `0.159.2`.
- Each profile's fresh deployed MCP process exposed all five `a2a_*` tools.
- Existing HTTPS listeners and configured authenticated peers were used.
- Installed profile manifests: `a2a-https-hotfix` 1.0.0 and
  `codex-app-server-phase-hotfix` 1.8.10. No upgrades or restarts in this probe.
- Actual target turn contexts: default/product `gpt-6.1-sol / high`,
  procurement `gpt-5.6-sol / high`.
- Each call used a new context to avoid the separate per-context A2A turn budget.
- Model context was not just the short marker prompt: observed per-response
  input was 20,764–26,152 tokens, including profile instructions/tool context.
- This controlling desktop conversation shared the account; account-wide traffic
  was not isolated. The measured concurrency below counts test A2A turns only.

## Method and results / 方法与结果

A temporary harness opened each profile's real stdio `hermes-tools` MCP with its
own `HERMES_HOME` and Codex configuration. It invoked the existing `a2a_call`
handler over the configured HTTPS route. No alternate provider/API key was used.
Each outer response had to contain the exact marker. Target session events were
then checked for model, start/end time, final text, token usage and terminal error.
This second check matters because the older local error wrapper alone can report
transport completion without proving model success.

| Test | Receiving A2A tasks | Result |
|---|---:|---|
| Serial, one target per local profile | 3 | 3/3 exact replies |
| Two profiles concurrently, both GPT-6.1 Sol | 2 | 2/2 |
| Three profiles concurrently, three batches | 9 | 9/9 |
| product model invokes procurement through `a2a_call` exactly once | 2, outer + inner | 2/2; actual tool invocation verified |
| Three distinct contexts concurrently on product, all GPT-6.1 Sol | 3 | 3/3 |
| Total | **19** | **19/19** |

There were 18 harness MCP calls plus one agent-initiated inner A2A call. An
additional automatic tool-approval review was observed and excluded from the
19 A2A tasks. The nested chain finished in 21.355 seconds. Direct marker calls
finished in 5.345–11.944 seconds.

Target logs confirm actual turn overlap, not just simultaneous client dispatch:
three same-profile turns overlapped from 08:59:07.086 to 08:59:11.871 UTC.
The maximum observed test-target starts in any 60-second interval was nine.
An active turn can include startup/tool waits: this is not a measurement of
simultaneous server-side token decoding. The figure nine is not a discovered RPM
limit; a turn may contain multiple provider requests and internal retries.

## Quota / 额度

Before and after the tests, each profile's `account/read` reported ChatGPT Pro,
and `account/rateLimits/read` reported:

- The same account identity (compared privately; identifiers omitted here).
- One returned bucket, `codex`, with a 10,080-minute weekly window.
- `usedPercent = 8`, `secondary = null`, `ordinaryUsageAllowed = true`.
- `rateLimitReachedType = null`, `spendControlReached = false`.

Unchanged integer usage does not imply zero consumption; rounding, delayed
reporting and unrelated account activity prevent attribution from this delta.
The interface does not expose an RPM/TPM or concurrency cap in this response.
See the official [App Server rate-limit fields](https://learn.chatgpt.com/docs/app-server#6-rate-limits-chatgpt).
The current [pricing documentation](https://learn.chatgpt.com/docs/pricing)
states that Pro has no five-hour limit and weekly limits may apply; it does not
establish unlimited throughput or a per-profile allowance.

## Historical error boundary / 历史错误边界

The four examined Oct 14 failures ended with:

```text
exceeded retry limit, last status: 429 Too Many Requests
codex_error_info.response_too_many_failed_attempts.http_status_code = 429
```

The product failure used `gpt-6.1-sol / high`; three procurement failures used
`gpt-6.1-sol / max`. Current procurement uses `gpt-5.6-sol / high`, so this probe
is not an exact replay of its historical workload. Those completion records do
not expose `usage_limit_reached`, Retry-After, reset metadata or a server-classified
rate-limit reason. The separate Windows quota-exhaustion incident is not evidence
for the cause of these local failures.

No failure occurred to support a causal cooldown, model, token-throughput or
concurrency comparison. Distinguishing those explanations requires a future real
failure with structured provider error/retry metadata and synchronized request
activity. This probe does not add permanent instrumentation or polling.

## Preservation and evidence / 保留与证据

SHA-256 comparisons confirmed all three profiles' Hermes YAML, `.env`, Codex TOML
and auth files unchanged. No reset credit was consumed, and no credentials, models,
Gateways or deployment files were changed. Nothing was sent through QQ/WhatsApp.
No code fix or deployment rollback is needed; only disposable test conversations
and private evidence files were added. No publish or push was performed.

Private evidence is in `~/.hermes/backups/a2a-local-rate-20261016/`: per-call JSON,
per-profile inventories/quotas, selected target runtime events, preservation
checks, summary and the temporary probe scripts. It may include private account
metadata and must not be committed. This report intentionally excludes credentials,
account identifiers, machine addresses and original conversation content.

Not tested: sustained load, more than three concurrent A2A tasks, long generated
outputs, exact historical procurement model/reasoning settings, full HTTP retry
headers, same-context turn-budget boundaries, or recipient-visible QQ delivery.
