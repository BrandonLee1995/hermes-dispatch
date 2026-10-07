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
