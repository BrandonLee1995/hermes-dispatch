# WeKnora QQ acceptance and release integration — 2026-10-13

## Accepted scope

The release combines WeKnora routing 1.0.3, QQ compatibility 1.8.41 and the
finance approval fixes promoted into Codex compatibility 1.8.10. Hermes core,
endpoint credentials, approval policy and reasoning settings are unchanged.
WeKnora is available as an optional release plugin; updates require explicit
profile opt-in, and the default installer omits it.

## Finance private-chat evidence

A new QQ private session and native thread excluded prior group context. The
initial 1.0.2 run successfully searched owned and shared libraries, but one new
shared target was declined by Auto-review because the retained evidence did not
establish that target's read authorization. Its previous inventory had returned
viewer access; the compact review input omitted that permission field.

Version 1.0.3 requires fresh paired inventories and concise scope evidence for
each retrieval-bearing user turn. The same private thread retained the denial.
After fresh evidence, the exact previously denied target passed the unchanged
reviewer. No alternate transport, approval override or policy relaxation was
used. The original failure remains separate from the successful retest.

| 1.0.3 observation | Result |
| --- | --- |
| Colloquial business turns | 3/3 completed |
| Successful searches | 6/6: 4 shared, 2 owned |
| Paired inventory in each search turn | Confirmed |
| Search targets and returned KB scope | Within the current inventory |
| Translation, rewrite and explicit no-KB turns | 3/3, no KB tool calls |
| Explicit scope question | 1 owned + 7 shared, shared total complete |
| Final replies visible in QQ | 7/7 |
| Finance approval plugin before/after | All source hashes unchanged |

Business cases covered packaging, onboarding and refund documentation. The explicit scope question
was asked after the business cases so it did not prime the routing prompts. Prompts did not name MCP tools, KB IDs or exact documents.
The model distinguished retrieved policy from general advice and stated limits
where complete company policy was not found. This is not an exhaustive factual
accuracy audit or a universal routing-success estimate.

Finance's QQ plugin was separately updated from 1.8.38 to 1.8.41 after detecting
the already-reproduced duplicate-commentary issue. Its existing 1.8.9 approval
plugin, WeKnora connection and configuration were preserved. No KB writes were
performed; raw knowledge content, identifiers and credentials are not exported
in this record.

## Comparison profile

The requested domestic-business host was offline; comparison switched to the
C-side product-development default profile at the operator's request.

| Read-only configuration observation | Finance | Comparison default |
| --- | --- | --- |
| Hermes source | 0.21.0, same commit | 0.21.0, same commit |
| Hermes configured model/runtime | gpt-6.1-sol / codex_app_server | Same |
| Codex CLI | 0.160.1 | 0.159.2 |
| Base approval settings | on-request / auto_review / workspace-write | Same |
| Codex compatibility plugin | 1.8.9 finance fixes | 1.8.8 |
| WeKnora routing plugin | Enabled, Hermes mode | Absent |
| WeKnora declarations | Hermes and Codex | Codex only |

The comparison is not an equivalent hook-enabled A/B test. Its standalone
Codex default model also differs from its Hermes-selected model; that is not
proof of the model used by a particular Hermes turn. The 0.159.2 protocol schema
was checked and exposes approvalsReviewer on thread/start, thread/resume and
turn/start. No configuration or running service was changed on that host.

The finance denial/recovery supports the missing-review-evidence explanation,
not a claim that model choice or a Hermes version difference caused it.
[Official Auto-review documentation](https://learn.chatgpt.com/docs/sandboxing/auto-review#what-the-reviewer-sees)
explains the compact transcript boundary and denial handling.

## Regression and deployment boundaries

- All 31 declared release scripts passed in isolated HERMES_HOME environments
  on local macOS and native Windows.
- Red/green checks cover current-turn scope instructions and updating an
  explicitly selected optional plugin without enabling it elsewhere. Existing trust, endpoint/profile,
  permission-response, reviewer, delivery, streaming and updater tests passed.
- Review follow-up covers actual Hermes legacy migration after default install,
  optional-plugin selection across absent/enabled/disabled configurations, and
  full updater preservation of newer local fixes.
- Earlier two-host QQ long-task acceptance established Working-only bubble
  boundaries; subsequent receipt/duplicate fixes were verified with real QQ
  tasks on local macOS and the product-R&D Windows computer.
- The historical 72-trial ablation used the older routing policy and is not a
  fresh performance or accuracy estimate for 1.0.3. Native Codex-side mode was
  not rerun in the finance retest, which deliberately used Hermes mode.

The shared approval release imports the tested finance approval module and its
regressions while retaining main's newer long-turn implementation. It does not
replace the live finance approval module as part of this acceptance. No fleet
rollout or automatic PR merge is included. Use each profile's installer/updater
backup and original service domain for rollback; see the owned plugin READMEs
and operations guide for enablement and verification steps.

## PR review follow-up

GitHub had no submitted reviews or status checks when inspected. Two additional
cases were reproduced during local review: the default installer made legacy
Hermes migration enable the newly copied hook, and a repair installed while
regressions ran could be overwritten using stale version decisions. Both have
red/green regressions. Default installation now excludes the optional hook; the
updater selects it only from explicit enabled/non-disabled profile state, and
defers before live mutation if plugin content or selection changes during tests.
The current finance 1.8.9 plugin was compared file-by-file with the PR's
1.8.10 release: runtime code and dedicated approval tests match. README, version
metadata and a deterministic-clock change in the generic regression differ. Read-only selection probes excluded WeKnora from the comparison host's
default, product and procurement profiles and retained finance's explicit opt-in.
All 31 release scripts passed again on macOS and native Windows; the updater
suite has 34 tests, with the POSIX installer migration test skipped on Windows.

The legacy-file check also covers a previously copied optional plugin with no
selection list: update stops before any Hermes CLI call could grandfather it.
Explicitly disabled legacy copies remain untouched and do not block updating
required plugins.

A Windows rerun exposed an intermittent deadline assertion in an existing
30 ms sleep-based test. The test now supplies before-deadline and at-deadline
clock values explicitly; production timeout code is unchanged.

## Authorized fleet opt-in follow-up

The operator subsequently authorized enabling Hermes-side routing on other
profiles only when their own existing WeKnora MCP is reachable. A separate
capability-gated preparation command now performs real HTTP MCP tool discovery
and successful owned/shared inventory reads before writing the profile's explicit
selection. Ordinary updater/install defaults remain unchanged. Six CLI/HTTP
boundary cases cover successful dry-run/apply and exact backup preservation,
access failures, disabled/absent connections, active work, missing retrieval
capability, endpoint mismatch, and named-profile isolation. Both macOS and native
Windows pass these cases; the full release matrix passes all 32 scripts on both.
Fleet deployment/connection evidence is recorded separately from this prior
Finance QQ acceptance; these automated checks do not claim new QQ conversations.
