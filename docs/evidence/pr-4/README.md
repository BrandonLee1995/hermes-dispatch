# PR #4 review evidence

This bundle contains redacted **observations**, not generated replacement
messages. No bot identity, QQ user/group ID, account name, session ID, token,
credential, or local profile path is included.

## 1.8.19 long-run acceptance (2026-08-29)

- [Gateway interval](2026-08-29-long-run/gateway-interval.txt): all Gateway log
  lines from first stream open through response-ready, with identifiers replaced
  by stable placeholders. Timestamps use Asia/Shanghai. Absence-of-error claims
  apply only to this bounded interval, not the entire production log.
- [QQ capture](2026-08-29-long-run/qq-capture.json): actual text of the four
  native response carriers, re-read from the macOS QQ accessibility tree on
  2026-08-31. Accessibility flattens line breaks to spaces. This is a text/AX
  capture, **not a screenshot or a fresh 1.8.20 long run**. User-authored test
  instructions, unrelated history, UI chrome and separate `Working` heartbeat
  messages are omitted; none of the four native carrier bodies is abbreviated.
- [Verification script](verify_long_run.py): independently reconstructs the
  requested synthetic output, checks the complete captured response for exact
  equality (including all 96 lines), marker counts, carrier sizes and lifecycle
  counts. It also checks reconnect, final suppression and response metrics.

| Carrier | Opened | Completed | Cause | Visible characters |
| --- | --- | --- | --- | ---: |
| 1 | 00:36:44.077 | 00:44:44.564 | silent 480.5s deadline | 78 |
| 2 | 00:44:54.885 | 00:50:47.731 | overflow | 4000 |
| 3 | 00:50:48.237 | 00:50:59.816 | overflow | 4000 |
| 4 | 00:51:00.298 | 00:51:06.784 | turn final | 2318 |

The same turn encountered a natural WebSocket `4009` close at 00:47:47.731;
connected at 00:47:49.904 and resumed at 00:47:50.136. The Gateway reported
872.5 seconds and a 10,234-character final. The four native bodies contain 162
progress characters followed by that final; each final marker appears once.
All four carriers sealed; none needed retirement. No lifetime, stale-index,
passive-reply-budget error, fallback or retry-storm log occurs in the interval.

Run from this directory:

```bash
python3 verify_long_run.py
```

## 1.8.20 review correction

The 1.8.19 live pass above does not disprove the independent-final suffix bug
reported in the 2026-08-31 review. That negative is now reproduced through the
real `GatewayStreamConsumer` and fixed separately. Both new real-client turns
below passed on procurement after installing 1.8.20 and restarting only that
Gateway. Token refresh, WebSocket connected and Ready were observed at
09:33:35. Repo/installed `streaming.py` SHA-256 both were
`cbe53970ca511c61e319a7113e5915d4bc842def081f2dc494a115dd958665bc`.

1. **Suffix turn**, 18.2 seconds: one native bubble progressed from
   `status NOTFINAL` to `status NOTFINALFINAL`, then sealed with no ordinary
   final. [AX capture](2026-08-31-review-retest/qq-suffix-capture.json),
   [Gateway interval](2026-08-31-review-retest/gateway-suffix.txt).
   The sidebar preview is excluded from the count. `FINAL` occurs once inside
   the commentary word and once as the actual final; those are intentional.
   Codex emitted final deltas in this run, so this is not evidence of injecting
   a final-without-deltas failure. That negative is tested deterministically
   through the real consumer, with and without completed commentary.
2. **Overflow turn**, 62.6 seconds: two commentary segments plus a 10,288-char
   final, all 96 requested lines intact. [AX capture](2026-08-31-review-retest/qq-overflow-capture.json),
   [Gateway lifecycle excerpt](2026-08-31-review-retest/gateway-overflow.txt).
   The three native carriers contain 4,000 / 4,000 / 2,305 characters including
   17 progress characters. `R20_BEGIN` and `R20_OK` each occur once. All three
   carriers sealed and the Gateway suppressed the ordinary final.

| Carrier | Opened | Completed | Cause |
| --- | --- | --- | --- |
| suffix-1 | 09:34:11.297 | 09:34:22.284 | turn final |
| overflow-1 | 09:34:48.154 | 09:35:21.280 | 4000-char overflow |
| overflow-2 | 09:35:21.821 | 09:35:38.368 | 4000-char overflow |
| overflow-3 | 09:35:38.896 | 09:35:48.411 | turn final |

The overflow excerpt deliberately includes only this turn's lifecycle and
completion lines: a different C2C turn opened concurrently at 09:35:30.696 and
is not this test. Its body, identity and lifecycle are excluded. A separate
full-window check found no error, fallback, stale-index, lifetime or passive
reply-budget error in either new test interval. These short tests do not claim
another 12-minute/reconnect run on 1.8.20. The historical long run is attached
above, not relabeled as a new-version run.

`verify_long_run.py` also checks both new captures. Full QQ, Codex hotfix,
snapshot-store, installer, Hermes 0.20.0 fail-closed and static matrices passed;
streaming tests also passed against the actual installed Hermes source. The
README, deployment guide and development log describe enable/verify/rollback.
Default/product and all credentials/configuration were untouched.
