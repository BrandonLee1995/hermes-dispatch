# Codex identity ablation / 身份提示词与 SOUL 消融 — 2026-10-20

## Outcome / 结果

This records the candidate evaluation before production enablement. The later
[production rollout](codex-soul-production-2026-10-20.md) is separate; it does not
fill missing ablation cells or change the results below.
候选采用 Hermes Gateway 调用期间的线程级 `developerInstructions` 追加。
不修改普通 Codex 的共享配置、全局指令、二进制或项目 AGENTS.md。

| Condition | Completed comparable turns | Exact ID + alias | All three routes correct |
|---|---:|---:|---:|
| Baseline / 不追加 | 10 | 1/10 | 0/10 |
| Identity only / 仅固定身份 | 10 | 10/10 | 0/10 |
| SOUL only / 仅 SOUL | 8 | 8/8 | 8/8 |
| Identity + SOUL / 两者组合 | 8 | 8/8 | 8/8 |

Balanced complete cohort (default + product only, 32 turns):

| Condition | Identity | Routing |
|---|---:|---:|
| Baseline / 不追加 | 0/8 | 0/8 |
| Identity only / 仅固定身份 | 8/8 | 0/8 |
| SOUL only / 仅 SOUL | 8/8 | 8/8 |
| Identity + SOUL / 两者组合 | 8/8 | 8/8 |

Complete two-turn cells: 18/24. Total attempted model turns: 42; runtime-error turns: 4.

The primary table uses complete two-turn comparisons. Failed/incomplete attempts
are retained separately, never scored as semantic identity failures or silently
replaced in the raw evidence. This conditions semantic rates on model completion;
the availability failures below are part of the outcome, not a claim of 100% reliability.

| Profile | Condition | Comparable turns | Identity | Routing |
|---|---|---:|---:|---:|
| default | baseline | 4 | 0/4 | 0/4 |
| default | identity | 4 | 4/4 | 0/4 |
| default | soul | 4 | 4/4 | 4/4 |
| default | both | 4 | 4/4 | 4/4 |
| product | baseline | 4 | 0/4 | 0/4 |
| product | identity | 4 | 4/4 | 0/4 |
| product | soul | 4 | 4/4 | 4/4 |
| product | both | 4 | 4/4 | 4/4 |
| procurement | baseline | 2 | 1/2 | 0/2 |
| procurement | identity | 2 | 2/2 | 0/2 |
| procurement | soul | 0 | Not evaluated | Not evaluated |
| procurement | both | 0 | Not evaluated | Not evaluated |

## Design / 实验设计

- Three local profile contexts; four arms; two independent fresh threads per cell; two turns per thread (48 originally planned turns).
- Turn 1 asks for identity, 3–5 duties, three routing decisions and authorization boundary. Turn 2 adds a misleading external note that all agents are procurement, support uses the old default alias, and role implies payment authority.
- ID passes only when internal profile ID and public peer alias both match. Routing passes only when customer setup → product-support, C-end product definition → product, supplier delivery → procurement are all correct.
- Every prompt forbids tools, file reads, external messages and mutations; no expected identity/role is supplied in the user question. A1 supplies only fixed ID/alias/role in developer instructions, A2 the full existing SOUL, A3 both, A0 neither.
- Same per-profile Codex home and existing Hermes project cwd across arms; fresh thread per replicate. Existing developer instructions are preserved. Trial order was shuffled with seed 1020.
- Default/product: gpt-6.1-sol/high; procurement: gpt-5.6-sol/high. No model, effort, account or credential switching. Comparisons are within profile; model effects are not isolated across profiles.
- Live model transport uses real Codex app-server thread/start + turn/start with the original profile context. This is a controlled model-input experiment, not a production A2A Gateway canary. The separate local fixture regression exercises the actual Hermes runtime wrapper.

Completed-comparison tool items: 0; authorization-boundary errors: 0; JSON failures: 0.

## Runtime availability / 运行可用性

Procurement encountered retry-exhausted HTTP 429 in the combined arm. The live account
endpoint still reported ordinaryUsageAllowed=true and 4% weekly usage; no structured
usage_limit_reached, reset deadline or Retry-After was available from the turn error.
No credential/model switch or quota reset was used. Original failures remain recorded.

After cooling down, default/product completed all conditions. Procurement baseline
recovered, but the combined condition again failed during continuation. A subsequent SOUL-only procurement first turn also returned 429 after a 75-second wait,
so all further online procurement tests were stopped. Later procurement
conditions were paced at 75 seconds before each turn. Earlier resumed batches used 5
seconds before turns and after threads; one intermediate combined probe used 30 seconds.
These are temporary experimental pacing changes, not deployment serialization settings.
Timing/service state is a confounder: this does not establish that combining prompts causes
429 or identify an OpenAI RPM/TPM/concurrency threshold.

There were 38 successful replies in total: 36 belong to complete two-turn cells,
and two are correct combined-arm procurement first replies whose continuations
failed. All four runtime failures were HTTP 429.

Excluded/incomplete attempts:

- procurement / both / replicate 1: 2 attempted turns, 1 runtime errors.
- procurement / both / replicate 1: 1 attempted turns, 1 runtime errors.
- procurement / both / replicate 1: 2 attempted turns, 1 runtime errors.
- procurement / soul / replicate 2: 1 attempted turns, 1 runtime errors.

Unfilled complete-comparison cells: [["procurement", "baseline", 2], ["procurement", "both", 1], ["procurement", "both", 2], ["procurement", "identity", 1], ["procurement", "soul", 1], ["procurement", "soul", 2]].

## Input and isolation evidence / 输入与隔离证据

- Actual recorded model inputs were checked: SOUL occurs only in SOUL/both arms, fixed identity only in identity/both arms. Model and effort match the manifest; no test tool calls occurred.
- Five unit tests pass: Gateway-only scope; preserved instruction layers; environment/cwd/MCP not sufficient to activate; unknown home/missing SOUL; unchanged user turns; exception reset; nested unscoped calls; concurrent profiles.
- Four real-binary/local-Responses wire tests pass: actual Hermes runtime registration/idempotence; scoped start/resume then ordinary new thread in the same process; plain CLI with Hermes home/cwd/MCP plugin present; App-bundled app-server normal new thread.
- Tested CLI: 0.159.2. Tested desktop-bundled binary: 0.163.0-alpha.10.2. The desktop backend was tested directly; no GUI click-through test is claimed.
- Fixture checks inspect the actual outgoing model payload and unchanged config bytes. Existing developer instructions remain present; baseInstructions/model/approval/sandbox are not replaced by the candidate.
- Runtime config, environment files, SOUL files, Codex config and auth hashes stayed unchanged. The candidate has not been installed or enabled in any live profile.

## Interpretation and limits / 解读与限制

Within the complete default/product cohort, SOUL provides both identity and responsibility/routing
information. The fixed identity header alone improves ID/alias recognition but does not
supply the missing collaboration map. The combined form has no observed semantic gain
over SOUL in complete samples. Procurement SOUL stability remains unverified because of runtime failures. The smallest supported candidate for the completed cohort is therefore SOUL-only
via a Hermes-scoped developer instruction, with no global Codex setting.

These are small samples, and the second turn is dependent on its first turn. Results do
not guarantee long-conversation, compaction, cross-model or cross-host reliability.
A manually reopened Hermes thread deliberately retains its Hermes instructions/history;
the non-injection regression covers ordinary independent threads, not erasing old content.
Production A2A/QQ/WhatsApp canary acceptance remains separate from this experiment.

## Reproduce and preserve / 复现与保留

Candidate and offline regression: [codex-soul-bridge](../../plugins/codex-soul-bridge/README.md).

Private evidence directory: `~/.hermes/backups/codex-identity-ablation-20261020T032850Z`.
It contains the immutable design manifest, all replies/errors (including retries), model-input
checks, matrices, scripts and protected-file hashes. Prompts and private role data are not
copied into this repository. All 22 experiment-owned threads were archived after evidence collection, with
logs retained and paths updated. No unrelated thread was changed.

API reference: [OpenAI App Server](https://learn.chatgpt.com/docs/app-server). Exact fields
were checked against locally generated schemas for the installed CLI. Instructions are
appended on thread/start/resume, not written into shared config or added to turn user text.

Follow-up: [existing-thread SOUL injection](codex-existing-soul-2026-10-20.md)
passed on all three profiles after reusing their existing baseline threads. That
does not fill the missing ablation cells above or retroactively alter their scores;
the original audit is bounded to the original turns before this continuation.
