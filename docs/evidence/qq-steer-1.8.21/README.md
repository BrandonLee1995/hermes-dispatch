# QQ accepted-steer display segments — 1.8.21

## Build and scope

Validated on 2026-08-31, Asia/Shanghai, using the real local QQ client and
Hermes 0.20.5. Local branch: `codex/qq-steer-display-segments`, based on main
`243469226f765f05f8a39bf44fca01db5f1318d9`. This evidence describes the canary-tested
1.8.21 source submitted for review, not a merged release.

Only procurement was installed/restarted. Its installed `steering.py` and the
tested source both have SHA-256
`5c2e24ad4e99bc1f146b26a832b195db7d16f1682e053f4761bf473b92c461d7`.
Default/product Gateway PIDs and plugin hashes stayed unchanged. Procurement
config.yaml and .env hashes also stayed unchanged. No credentials, memories,
session migration, Codex login changes or direct Hermes source edits were made.

## Real-client results

Messages below are test-only marker transcriptions from the QQ message-list
accessibility tree, with the private result also checked visually. They are
not full chat exports. User instructions and sidebar previews are excluded
from final-marker counts. No account/chat/session identifiers are included.

| Case | Visible bot-message order | Result |
| --- | --- | --- |
| C: private `/steer` | `R21_BEFORE_C` → command ACK → `R21_AFTER_CR21_DONE_C` | Pass; old carrier frozen, new carrier below ACK, final once |
| D2: private ordinary correction | `R21-D2-BEFORE` → Redirected ACK → `R21-D2-AFTERR21-D2-DONE` | Pass; old carrier frozen, new carrier below ACK, final once |
| G2: group actual @mention correction | `R21-G2-BEFORE` → Redirected ACK → `R21-G2-AFTER` → `R21-G2-DONE` | Pass; existing ordinary group transport, final once |

For each case, start a text-only task: emit its BEFORE marker, run a bounded
sleep, and otherwise emit OLD-DONE. After BEFORE is visible, send a correction
asking for AFTER, sleep 2, then DONE, without file/business/network operations.
Use `/steer` for C; ordinary text for D2; a real selected bot mention for both
G2 inputs. All three accepted cases returned DONE instead of OLD-DONE.

The Codex transcripts contain the correction as a new user input in the
existing task, AFTER commentary, DONE final and `task_complete`. No replacement
Codex task was created by steering. Completion times (local) were:

- C: correction recorded 11:51:25.239; final 11:51:38.424;
  task complete 11:51:38.489; QQ final seal 11:51:40.047.
- G2: correction recorded 11:55:23.754; final 11:55:38.451;
  task complete 11:55:38.545; Gateway response 11:55:38.559.
- D2: correction recorded 12:00:19.869; final 12:00:26.519;
  task complete 12:00:26.570; QQ final seal 12:00:28.180.

Steering does not promise instant tool cancellation: D2's Gateway accepted the
correction at 11:59:48.858, while the native backend recorded it at 12:00:19.869
around the tool boundary. This delay was observed, not hidden by a new turn.

### Gateway carrier evidence

These are lifecycle facts from the procurement Gateway log, with carrier IDs
replaced by per-case labels. Frames include the terminal seal.

| Case | Old open | Old seal | Segment rotation | New open | New seal |
| --- | --- | --- | --- | --- | --- |
| C | 11:50:54.514 | 11:51:21.019 (2 frames) | 11:51:21.020 | 11:51:30.186 | 11:51:40.047 (4 frames) |
| D2 | 11:59:17.399 | 11:59:49.242 (3 frames) | 11:59:49.242 | 12:00:22.653 | 12:00:28.180 (4 frames) |

Gateway confirmed normal-final suppression for C at 11:51:40.052 and D2 at
12:00:28.186. G2 used the existing ordinary group final, not C2C native frames.
The final-build interval from QQ Ready at 11:50:16.091 through D2 completion
contained no `40034128`, native fallback, seal-retirement warning, or QQ error.
The restart's SIGTERM warning belongs to the intentionally stopped old process.

## Failures and excluded attempts

- First canary B: explicit `/steer` changed the bubble but its correction never
  reached Codex; the old final appeared. Root cause: Hermes `steer()` queues
  work for Hermes-owned tool batches, which the Codex-owned turn does not drain.
  A failing native-steer regression was added before routing only active
  QQ/Codex steering through the existing native `redirect()` implementation.
  Post-fix case C passed; an ACK alone is never counted as acceptance.
- First ordinary A passed on the earlier build. An in-flight old tool-step
  marker appeared after redirect, before corrected output. It was newly
  produced backend output, not replay of the old visible prefix. D2 validates
  ordinary steering again on the final build.
- The first group attempt and private D completed before a correction was
  sent. Neither counts as steering acceptance. G2 and D2 replaced them.
- Group text entry initially dropped Chinese characters; clipboard insertion
  also timed out. The test draft was corrected and G2 used ASCII text with an
  actual selected bot mention. This was a client automation issue, not a
  successful steer or a production transport failure.

## Automated verification

All 27 `test_steer.py` checks passed against both the 0.20.5 source fixture and
the actual installed Hermes source. They exercise real busy handlers, consumer
and adapter lifecycle, with only the external QQ wire and native request
boundary replaced. Coverage includes repeated/concurrent steering, rejected
and unauthorized inputs, debounced/failed ACKs, cancellation, stop, same-tick
final, same-text independent final, seal failures, deferred output, overflow,
late callbacks, rebased fallback, barrier timeout queueing, group native
control and non-QQ isolation. Red tests preceded the implementation changes.

The complete documented matrix passed: seven QQ scripts (including 81 streaming
checks), Codex phase hotfix, five snapshot scripts, WhatsApp policy hotfix
(test-only), two MCP wrapper scripts, installer tests, shell syntax,
compilation and `git diff --check`. Hermes 0.20.0 streaming/steering checks
passed their fail-closed path. These simulated boundaries do not claim live
network-fault or live carrier-lifetime coverage for this steer change.

## Enable and rollback

Install only the persistent QQ plugin with the existing native C2C settings,
then restart only the intended profile after tests pass. No new settings are
required. See the [plugin instructions](../../../plugins/qqbot-connect-hotfix/README.md)
and [deployment guide](../../macos-hermes-codex-deployment.md).
Use `scripts/install-plugins.sh --restore` with the exact pre-canary **1.8.20**
external backup, then restart that profile to roll back. Do not choose the
intermediate 1.8.21 backup containing the first failed explicit-steer attempt.

No remaining failure was observed in the final-build acceptance cases. Real
failure injection, repeated live steers and carrier-lifetime tests were not
rerun in this change; their boundaries are covered by automated regressions.
Default/product rollout was not performed. Remote submission for review does
not change the scope of this procurement-only acceptance.
