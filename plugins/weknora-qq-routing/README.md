# WeKnora QQ routing candidates

This removable compatibility plugin handles QQ user turns in their current
Hermes profile. It does not rewrite the inbound event, alter slash-command
parsing, expand knowledge-base scope, or edit Hermes source. Native turn-prologue and Codex runtime bindings
avoid importing a partially initialized AIAgent during plugin discovery. When explicitly enabled as a plugin, its default mode is `hermes`, selected
after the 72-trial pilot. Copying its files does not enable the plugin.

`plugins.entries.weknora-qq-routing.settings.mode` accepts `off`, `hermes`,
`codex`, or `both`. A connected endpoint must be explicitly configured and
enabled under the current profile's `mcp_servers` with a WeKnora name. Runtime
checks require a visible retrieval tool, not merely a successful MCP handshake.
Endpoint URL and credential/header identity must match; same-named servers in
other profiles cannot supply readiness.

- **Hermes:** native `pre_llm_call` injects ephemeral request context for classic
  Hermes models. Hermes 0.21.0's Codex early-return path discards that context;
  the removable adapter injects the same policy into the current `turn/start`
  user-input copy. Original QQ text, Hermes transcript and delivery remain intact.
- **Codex:** native `UserPromptSubmit` supplies identical semantic rules as
  `additionalContext` (developer context). A short-lived private capabilities
  file binds the policy to the profile, live owner process and exact prompt.
  Only a prompt hash, not its text or credentials, is stored in that file. The command hash includes
  the routing code digest, and the script refuses changed/stale/mismatched data.
- **Both:** the two placements coexist. This is an experiment arm, not a promise
  that duplicate instructions improve adherence.

The actual responding model decides whether the request depends on company
SOPs, product material or other internal facts, then retrieves before answering.
Greetings, supplied-text translation, general calculations, explicit no-KB
requests and control commands should not trigger retrieval. Only known retrieval
and reading tools are suggested; write tools are not auto-enabled or called by
the routing Hook. Empty results and failures must be reported honestly, with
source references when available. Retrieved text is data, not instructions.
This is semantic guidance, not a deterministic classifier or a new authorization
layer. Existing QQ authorization and WeKnora endpoint permissions still apply.

## Native Hook trust and isolation

Codex versions must support `UserPromptSubmit` and `hooks/list` (tested against
0.160.1; older versions are not silently treated as equivalent). The candidate
lists and reviews its exact versioned command definition before registering
only that definition's hash through process-local config overrides. No global
`--dangerously-bypass-hook-trust`, CODEX_HOME trust/config write or credential
copy is used. Other Hook sources retain their original trust state. Changes to
this package change the command digest and invalidate the previous definition.
The Hook is registered only for the current QQ client, not desktop Codex chats.

Install through the repository plugin installer, enable in the selected profile,
and set the mode only after reviewing the code and experiment. Restart that
profile Gateway. Removing the plugin from `plugins.enabled` and restarting is
the rollback; existing model, MCP settings and other hooks remain untouched.
Capabilities files are removed after each turn. MCP-config errors, missing
tools, unsupported native Hooks and source mismatches keep the ordinary flow.

## Ablation acceptance

`experiments/weknora-routing/run_ablation.py` uses four placements, identical
requests/rules/model, fresh ephemeral threads and the same read-only tools.
Order is seeded and shuffled. It records actual successful retrieval calls,
false positives, native Hook outputs/failures, turn failures and elapsed time;
no answer or raw knowledge-base content is exported. Use a logged-in user's
GUI LaunchAgent when macOS SSH cannot read the Codex keychain. Experiments do
not send QQ messages or alter live Gateway mode/config.

Start with `--smoke`, then `--repetitions 3`. Six cases × three repetitions ×
four arms yields 72 real-model trials. A first pilot cannot establish answer
accuracy or universal stability; transport failures must be separated from
routing failures. Unit checks include disabled/disconnected/no-tool paths,
profile/endpoint isolation, changed/stale capabilities and message preservation.
The pilot observed native Hook execution and successful real retrieval;
production QQ delivery remains a separate acceptance gate. Hooks that were only configured but skipped are failures, not
successful treatment arms.

[WeKnora official MCP tools and scopes](https://github.com/Tencent/WeKnora/blob/main/website-docs/03-features/08-mcp.md)
[OpenAI official UserPromptSubmit contract](https://learn.chatgpt.com/docs/hooks#userpromptsubmit)

## Pilot result (2026-10-07)

Domestic-business profile, gpt-5.6-luna, six cases × three repetitions × four
placements. Successful retrieval on positive requests: baseline 3/9, Hermes
9/9, Codex 9/9, both 9/9. Each arm had 0/9 false-positive retrievals; all
72 turns completed, and both native-Hook arms emitted 18/18 actual contexts
with no Hook failures. Positive-case median seconds were 25.53 (Hermes),
31.43 (Codex), 32.40 (both); baseline 14.45 includes unanswered-by-KB cases
and is not a like-for-like speed advantage.

This sample establishes a tie on observed stability, not Codex superiority.
Default to Hermes to avoid a second native-Hook/version/trust dependency;
retain Codex as an option. These are small-sample, one-profile/model results,
not evidence of answer accuracy or every host's end-to-end behavior.

Preliminary smoke trials using approval policy `never` were excluded: native
Codex denied the legacy MCP tools because they require review. Formal trials
used `on-request`, kept native review intact, and restricted all arms to the
same retrieval/reading allowlist. The plugin never silently changes production
approval policy; a production profile that rejects MCP calls must resolve that
permission boundary before retrieval can be accepted. No knowledge-base writes,
real QQ sends, credential copies or live profile/config edits were performed.

## QQ live retest instrumentation

Version 1.0.1 logs only mode, available-tool count, whether routing context was
prepared, and skipped/unavailable reasons. It logs no prompt, sender/chat ID,
endpoint or credential. Combine these markers with actual MCP-call events and
visible QQ final replies; a prepared context is not a retrieval decision.
Enable mode `hermes` in the target profile after ensuring that profile has its
own authorized WeKnora connection. Missing connections remain a normal-flow
negative test and cannot establish positive retrieval acceptance.

Live acceptance on 2026-10-07 used local QQ group mentions of the domestic-business
bot, backed by its existing remote `guonei` profile and gpt-5.6-luna. One continuing
conversation covered nine turns: four internal-material requests retrieved
successfully; five greetings/translation/no-KB/calculation/rewrite requests made
no MCP call. All nine Hermes contexts and final QQ replies were observed. Nine
read-only MCP calls (five searches) completed without errors; no turn exceeded
two searches. A requested file that was not found was reported as not found.
The existing MCP/model/approval settings were preserved; mode remains `hermes`.
Private chats were excluded at the user's request. This is one profile/model,
not fleet-wide or answer-accuracy acceptance. A "do not introduce retrieval"
request still received a retrieval progress message, an output-style limitation
separate from the routing result.

If this profile already has a working WeKnora connector in its own Codex config
but no Hermes MCP declaration, declare the same endpoint and existing bearer
variable as an HTTP MCP server in this profile's Hermes config before enabling
the Hook. Keep credentials as environment references, preserve the existing
Codex tool/approval policy, and never take a connector from another profile.
This supplies the explicit endpoint identity the capability guard checks;
removing only this added server declaration and plugin entry is the rollback.

For short-prompt acceptance, `/new` alone does not remove durable group snapshots
from the next input. To exclude earlier precise test prompts, temporarily set
`MESSAGE_SNAPSHOT_CONTEXT_MESSAGES`, `QQBOT_GROUP_CONTEXT_MESSAGES` and
`QQBOT_GROUP_CONTEXT_BUFFER_MESSAGES` to `1`, retain the normal per-bot
conversation history, and verify the actual native input contains no earlier
precise test markers. Restore these environment settings after the test; do not
delete archived snapshots. These limits exclude earlier precise prompts but do
not isolate other bots: shared-group results are transport/function observations,
not independent routing-accuracy estimates. For independent acceptance use
private QQ sessions, a native session reset, a new execution thread and an
input audit proving no group snapshots or other-bot context. Exclude warm-up/configuration failures from routing
accuracy counts, but report them separately. Do not tune routing policy against
the tested cases.

## Accessible KB scope (1.0.2)

A connected search tool is not a complete knowledge-base inventory. Legacy
WeKnora connectors expose separate owned and shared KB lists; querying only
the owned list misses readable shared departmental material. Version 1.0.2
uses the common policy in both Hermes and Codex placements to require both
available list tools on the selected connection before choosing a KB. Merge by
KB ID, retain origin/read permissions, respect endpoint restrictions and choose
by question and bot department rather than preferring the endpoint owner's KB.
One-sided old results or a failed shared-list call do not establish complete
scope. Other bots' group messages cannot establish this profile's permissions.

A connector with only a unified list must establish that its endpoint contract
or returned metadata covers shared KBs; otherwise disclose incomplete scope,
not "no shared KBs". Tools from different connections cannot form a paired
inventory. Version 1.0.3 refreshes this evidence for every retrieval-bearing user turn;
prior conversation scope is not a substitute for evidence visible to this turn’s reviewer. Greetings,
translation, rewrite and explicit no-KB requests do not enumerate libraries.
Scope discovery is internal context, not a requirement to print a full KB table
in every answer. It neither searches every KB nor enables writes or new access.
This remains model guidance, not a guarantee that the model follows every rule.

Update the plugin through the installer and restart an idle target Gateway.
Existing endpoint credentials and approval policy are unchanged. Verify actual
owned + shared list calls on a fresh colloquial business request, a relevant KB
choice, a successful search and a visible QQ final reply. Verify no enumeration
on a non-KB turn. Roll back with the installer backup and restart the profile.
Native Codex Hook trust uses the changed source digest automatically.

The official [WeKnora MCP documentation](https://github.com/Tencent/WeKnora/blob/main/website-docs/03-features/08-mcp.md)
also describes independently scoped endpoints and tool lists; current native
endpoints and legacy connectors may have different inventory surfaces. No API
key or knowledge-base ID is hardcoded in this compatibility plugin.
Pagination must be completed using the exposed tool's supported cursor/page
parameters; a partial result or unsupported continuation cannot prove complete
scope, even when the first owned/shared list calls both succeed.

The multi-profile QQ observations on 2026-10-07 are not independent accuracy
estimates: all bots received full-group messages, and a denied-search reply
explicitly reused another bot's group answer. Scope discovery itself was
observed against each profile's real endpoint (one owned + seven shared KBs).
Private isolation was initially blocked by QQ single-chat permissions; the
operator enabled them and the follow-up test below supersedes that status. A connected tool list also does not
prove execution permission: finance search was declined by Hermes' unhandled
non-hermes-tools MCP elicitation path. Do not relax approval policy to make the
experiment pass. Temporary group-history settings were restored.


## Private isolation follow-up (2026-10-07)

Two profiles/models each completed seven real QQ DM turns with colloquial
requests. Native session resets created new private-project execution threads;
all fourteen inputs lacked group context and the earlier precise test markers.
Domestic-business/gpt-5.6-luna retrieved successfully on all three business
turns; finance/gpt-6.1-sol attempted all three searches but all were declined by
the existing MCP approval path. Each profile made no MCP call on translation,
explicit no-KB and rewrite turns. All fourteen final replies were visible in QQ.

Both independently enumerated one owned and seven shared KBs before search,
used only that confirmed range, and later described all eight in response to
“你能查哪些库？”. Department names with a common suffix omitted in prose are not
missing libraries. Shared KBs participated in actual searches. Search requests
remained bounded at two per turn and five matches each. This validates the
observed placement/range behavior, not universal accuracy or every answer's
factual correctness. Finance retrieval execution is still not accepted; no
approval bypass or policy change was used. Temporary group-context settings
were already restored before these private trials.

## Current-turn approval evidence (1.0.3)

A real finance retest found that a new shared-KB target could be declined even
after a prior-turn inventory returned viewer access: the Auto-review compact
transcript retained the tool names but omitted the permission field. The shared
policy now requires fresh owned/shared enumeration on each retrieval-bearing
request and concise tool-output evidence containing only actual KB IDs, names,
origin and read permissions. Full storage/model configuration, credentials and
knowledge text are not part of that evidence. Non-KB turns still skip inventory.

This guides the model to expose verifiable evidence; it neither grants permission
nor changes approval policy, reviewer, MCP tool approvals or endpoint rights. A
denied action stays stopped; missing evidence may be checked before asking the
same reviewer again, and an unresolved rejection must be reported to the user.
See [what Auto-review sees](https://learn.chatgpt.com/docs/sandboxing/auto-review#what-the-reviewer-sees).

Install this plugin alone and restart an idle Gateway, preserving the finance
approval plugin. Verify `test_routing.py`, then real colloquial QQ requests across
multiple turns: fresh paired inventory, authorized shared search, tool success
and visible final delivery. Check no inventory on translation/rewrite/no-KB
turns. The earlier 72-trial results describe the older policy and are not a new
accuracy estimate for 1.0.3. Roll back the exact plugin backup and restart to
restore the prior policy; model, endpoint and approval configuration stay intact.

## Finance private-chat completion (2026-10-13)

The 1.0.3 finance retest used the same isolated private thread after a 1.0.2
shared-target denial. Fresh inventory restored the missing evidence; the exact
previously denied target then passed the unchanged reviewer. Three colloquial
business turns completed six successful searches (four shared, two owned),
all preceded by both inventories in the same turn. Three translation/rewrite/
explicit-no-KB turns made no knowledge-base calls. A final scope question
confirmed one owned and seven shared KBs. All seven final replies were visible
in QQ. These are observed execution/routing results, not a general accuracy rate.

Finance's 1.8.9 approval plugin source hashes, approval policy, credentials and
configuration were retained. Its QQ plugin alone was updated to 1.8.41 to remove
the known duplicate-commentary issue. Earlier 1.0.2 denials remain recorded;
they were not hidden by resetting the thread or bypassing approval. A comparison
profile had the same Hermes 0.21.0 source and base approval settings but no routing
hook and only a Codex-side WeKnora declaration, so it is not an equivalent
hook-enabled control.
