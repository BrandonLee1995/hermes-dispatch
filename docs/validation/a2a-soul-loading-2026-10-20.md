# SOUL identity acceptance / SOUL 身份加载实测 — 2026-10-20

## Result / 结果

**0/3 profiles passed the complete SOUL-loading/identity/routing check.**
SOUL files are saved correctly and accepted by Hermes' standalone loader, but
none of the three tested fresh Codex A2A sessions contained the new identity line
or responsibility paragraph in the recorded model input. The earlier file/loader
check did not establish runtime injection. Starting a new conversation alone is
insufficient on the currently installed path.

| Internal profile | Actual identity answer | Routing answer | Verdict |
|---|---|---|---|
| default | ID, peer alias and role all `unknown` | All three scenarios `unknown` | Fail |
| product | ID `product`; broad product-development bot role; peer alias `unknown`; duties do not match the new SOUL | Customer setup and product changes `unknown`; supplier delay → procurement | Fail |
| procurement | ID/alias `procurement`; procurement role | Customer setup → obsolete `default`; product changes → product; supplier delay → procurement | Identity partly correct; full check fails |

All three replies correctly said responsibilities do not automatically authorize
external messages, orders, contracts, payment or approval. This common boundary
alone is not evidence that SOUL was loaded.

## Method / 方法

On the three running local Hermes 0.21.0 Gateways at `29112bef09`, issue one
configured-peer HTTPS A2A request to each target, serially, using a new random
context ID. Use the same prompt without supplying the expected identity or
responsibilities. Ask the target to rely only on its already-loaded context,
return `unknown` when uncertain, and avoid tools, files, other peers or mutations.

The requested fields are internal profile ID, public A2A alias, role, at least
three responsibilities, routing for customer setup / C-end product definition
changes / supplier delivery delay, and the authorization boundary. Expected
routing is product-support / product / procurement respectively.

Target session records confirm fresh distinct projects/threads, zero tool calls
and no terminal runtime error. Exact SOUL text, its one-line identity mapping and
its responsibility paragraph were checked against the recorded initial metadata
and developer/user input messages as decoded strings. All three lacked the new
identity and responsibility content. A successful outer A2A result therefore
means transport/model completion, not successful identity acceptance.

## Read-only diagnosis / 只读定位

1. Most strongly supported: SOUL is not forwarded on this Codex runtime path.
   `agent/codex_runtime.py` constructs the session without Hermes system-prompt
   content and calls `run_turn(user_input=user_message)` (around lines 755–776).
   `agent/transports/codex_app_server_session.py` starts a thread with cwd only
   (around lines 346–348). The installed project-scaffolding patch creates generic
   AGENTS.md/project-memory guidance, without a SOUL bridge.
2. Reused old conversation context cannot by itself explain the result: all three
   requests used new random A2A contexts and new Codex threads. No Gateway restart
   was performed, so this test does not independently exhaust all cache hypotheses.
3. Existing Codex profile context can explain partial identity recognition, but
   cannot be counted as loading the newly synchronized SOUL. The new text is absent
   rather than merely contradicted by a competing instruction.

The ordinary Hermes prompt builder does support profile-scoped SOUL loading in
`agent/system_prompt.py` (around lines 475–484); that separate support does not
prove it is supplied to Codex app-server. The observed problem is between the
Hermes persona file and the model runtime, not A2A peer alias resolution.

## Evidence and scope / 证据与范围

Private evidence directory is recorded in the test run manifest under
`~/.hermes/backups/a2a-soul-test-*/`: prompt, three complete replies, exact target
session paths, input-presence checks and the acceptance evaluator. The evaluator
returns exit code 1 for this failed run. Raw model context is not copied into this
repository. Each A2A call took 9.240–18.290 seconds.

SHA-256 checks confirmed SOUL, Hermes config, .env, and Codex auth/config unchanged.
No fix was deployed, no Gateway was restarted, and no QQ/WhatsApp message was sent.
Only fresh A2A sessions were tested; existing QQ/WhatsApp sessions and other runtimes
were not accepted by this test. Next work is to bridge the correct profile's SOUL
into Codex instructions and repeat this same blind acceptance test.

Subsequent work evaluated an opt-in candidate through a controlled live-model
[identity/SOUL ablation and non-Hermes regression](codex-identity-ablation-2026-10-20.md).
It has not been enabled on production Gateways, so the failed live A2A baseline
recorded here is not superseded by a claimed deployment fix.
