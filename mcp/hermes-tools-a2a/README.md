# A2A tools for the Codex runtime

This opt-in stdio entrypoint extends Hermes' existing `hermes-tools` MCP with
`a2a_discover`, `a2a_list`, `a2a_call`, `a2a_history`, and `a2a_orchestrate`.
It preserves the other tools and the server name, and uses the upstream A2A
schemas and handlers. It does not start an A2A listener or configure peers.

The optional [peer directory plugin](../../plugins/a2a-peer-directory/README.md)
adds configured responsibilities to `a2a_list` through the same native registry.
Peer aliases and capability tags remain profile-local configuration; changing an
alias does not rename the internal Hermes profile or its authenticated identity.

## Why the compatibility layer exists

Hermes 0.21.0 (`29112bef09`) includes the A2A platform/client, but
`agent/transports/hermes_tools_mcp_server.py` has a fixed `EXPOSED_TOOLS` list
without A2A. Enabling A2A in Hermes alone does not expose it to a Codex
app-server turn. Hermes' tool-search compaction can also omit the individual
schemas from the default tool catalog.

The wrapper temporarily supplements that catalog with the **raw A2A toolset
only**, adds the five names to the exports, and calls the original server
builder. It restores the patched globals after construction, including on
failure. Existing upstream definitions take precedence, so future native A2A
exports are not duplicated. Registration, argument validation, dispatch,
authentication, conversation persistence, and errors remain upstream-owned.
No upstream source file is edited and no new dependency is needed.

The upstream availability check still applies: peers under `a2a_agents`, an
enabled A2A platform, or `A2A_PORT` opt the profile in. An unconfigured profile
gets no A2A tools. The MCP transport does not apply Hermes' per-platform
`platform_toolsets` list; use the owning Codex config's `enabled_tools` /
`disabled_tools` if a narrower MCP surface is required.

References:

- [Hermes A2A](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/a2a)
- [Hermes Codex runtime](https://hermes-agent.nousresearch.com/docs/user-guide/features/codex-app-server-runtime)
- [Codex stdio MCP environment forwarding](https://learn.chatgpt.com/docs/extend/mcp#stdio-servers)
- [Upstream MCP builder at the verified version](https://github.com/NousResearch/hermes-agent/blob/29112bef09/agent/transports/hermes_tools_mcp_server.py)

## Enable

Use the Python environment that runs Hermes, with its source importable and
its existing MCP SDK installed. The verified combination is Hermes 0.21.0,
Python 3.11.15, MCP SDK 2.0.0, and Codex CLI 0.159.2. This wrapper uses internal
Hermes builder APIs; rerun the checks below after upgrading Hermes.

1. As the profile's OS owner, copy `hermes_tools_a2a.py` into a persistent
   scripts directory, for example `$HERMES_HOME/scripts/hermes_tools_a2a.py`
   on the host or `/opt/data/scripts/hermes_tools_a2a.py` inside a container.
   Back up an existing copy before replacing it. Do not write files into the
   container's Hermes source tree or create root-owned files in mounted data.
   The plugin installer and fleet updater do not install this optional entrypoint.
2. Back up the owning `CODEX_HOME/config.toml`. Edit the **existing**
   `[mcp_servers.hermes-tools]` entry: keep its interpreter and other settings,
   replace its `args`, and add `HERMES_HOME` to `env_vars` without removing any
   existing forwarded names. Do not add a second server with duplicate tools.

Example (replace both absolute paths with the target runtime's paths):

```toml
[mcp_servers.hermes-tools]
command = "/absolute/hermes/venv/bin/python"
args = ["/absolute/persistent/scripts/hermes_tools_a2a.py"]
env_vars = ["HERMES_HOME"]
tool_timeout_sec = 300

[mcp_servers.hermes-tools.env]
HERMES_QUIET = "1"
HERMES_REDACT_SECRETS = "true"
```

`HERMES_HOME` must be absolute and belong to the calling profile. Codex does
not forward arbitrary parent environment variables to stdio MCP processes;
`env_vars` is essential. A relative home is rejected with code 2. If the home
is missing (for example, ordinary Codex Desktop sharing the default profile's
Codex config), the upstream tools remain available but A2A is withheld, so the
wrapper never silently exposes the default profile's A2A peers. For a
standalone Codex launch that needs A2A, export the intended `HERMES_HOME` first.
Do not pin the default profile's home in a shared MCP config. A multi-profile
Gateway must also supply the correct home when launching each Codex runtime.

Keep unrelated credentials, other MCP entries, sandbox/approval settings, and
per-profile `CODEX_HOME` choices unchanged. If a runtime cannot import Hermes
from the copied script's location, add its actual source directory as
`PYTHONPATH` in this MCP entry's `env`; use the interpreter from that install.

3. Install the [HTTPS-only A2A transport](../../plugins/a2a-https-hotfix/README.md)
   on the calling and receiving profiles, then configure peers in the caller's
   runtime-only `config.yaml`. An example without credentials is:

```yaml
a2a_agents:
  researcher:
    url: https://node.example.ts.net:9901/researcher
    timeout: 120
    capabilities: [research]
```

For authenticated peers, configure upstream's `auth` block locally; never
commit real tokens. The target profile separately needs its A2A listener,
distinct port, and authentication/trust settings. Consult the upstream A2A
guide for those settings. The MCP timeout must exceed the peer call timeout
plus Agent Card lookup time; the example allows 300 seconds.

4. Reload the MCP configuration / start a fresh Codex runtime when the owning
   session is idle. If enabling inbound A2A requires a Gateway restart, drain
   active work first. The wrapper does not restart anything itself.

## Verify

Self-contained startup patch regressions:

```bash
python mcp/hermes-tools-a2a/test_hermes_tools_a2a.py
```

Real MCP subprocess checks, using the installed Hermes environment:

```bash
HERMES_SOURCE_DIR=/absolute/hermes/source \
  /absolute/hermes/venv/bin/python mcp/hermes-tools-a2a/test_integration.py -v
```

To also exercise a real Codex app-server's MCP discovery and tool-call RPC:

```bash
CODEX_BINARY=/absolute/path/to/codex \
HERMES_SOURCE_DIR=/absolute/hermes/source \
  /absolute/hermes/venv/bin/python mcp/hermes-tools-a2a/test_integration.py -v
```

The integration checks use temporary Hermes/Codex homes and a local HTTPS test
peer. They check unchanged baseline tools, A2A opt-in gating, required arguments,
all five handlers, exact message/context forwarding, bearer-auth refusal, and
separate peer lists/conversation files for two profiles using the same context
ID. The optional Codex check uses `mcpServerStatus/list` and
`mcpServer/tool/call` in an ephemeral thread; it performs no model turn and
needs no account credentials. Without `CODEX_BINARY`, that check is skipped.

After a real deployment, verify separately:

- The affected Codex runtime lists all five A2A tools under `hermes-tools`.
- `a2a_list` shows only the intended profile's configured peers.
- A real A -> B -> A request returns the correct target's answer to the
  originating QQ/WhatsApp conversation. Confirm the profile, A2A context, and
  original-channel identity. A local test peer is **not** recipient acceptance.

An A2A conversation does not automatically share a profile's QQ/WhatsApp chat
history. Send the needed task context explicitly. The bridge also preserves
upstream's direct-URL calls (HTTPS only with the transport plugin): a peer list
is a directory, not an outbound network allowlist. Apply upstream/infrastructure
access controls if required.

## Roll back

Restore the prior `hermes-tools` arguments (normally
`["-m", "agent.transports.hermes_tools_mcp_server"]`) and only the other entry
fields changed during this installation, preserving subsequent unrelated
config edits. Reload the MCP configuration / replace idle runtimes and verify
the previous tool inventory before removing the copied wrapper. Revert any
new inbound A2A or peer configuration separately, using the owning profile's
backup and an idle Gateway restart where necessary. Do not remove conversation
history, credentials, or other plugins.

Once an upstream MCP bridge exposes these five tools correctly, passes the
profile-isolation checks, and works through the actual Codex runtime, restore
the upstream entrypoint and remove this wrapper.

## Error results

The startup wrapper now translates native A2A tool error envelopes into MCP
`CallToolResult(isError=true)`, preserving their text and the SDK's existing
`{result: string}` output schema. This covers authentication/transport errors,
argument errors, RPC errors and failed/rejected/canceled tasks when used with
`a2a-https-hotfix` 1.0.1. Successful reply bodies are not scanned for error words.
The other Hermes tools retain their upstream behavior. Orchestration `all` is
an aggregate report; its per-peer errors must still be inspected.

The workaround is needed because upstream Hermes returns tool failures as normal
strings. Update the wrapper file and A2A plugin together, restart the idle
Gateway/MCP children, and run `test_integration.py`; it verifies real MCP wire
responses for both successful and failed TLS fixtures. Roll back both components
from the deployment backup and restart if needed. Neither enabling nor rolling
back this change requires changing tokens or global model defaults.

Hermes' registry also catches escaped handler exceptions and serializes them as
JSON objects with a top-level string `error`. The wrapper recognizes these native
envelopes as well as the text prefixes, retaining the original serialized text
in both `content` and `{result: string}`. For example, a rejected Agent Card RPC
origin must return `isError=true` instead of a successful tool result containing
an error message. This follows the [MCP tool-error contract](https://modelcontextprotocol.io/specification/2025-11-25/server/tools#error-handling).
Successful peer replies containing quoted JSON error examples remain successful.

To apply just this correction, back up and replace the installed
`hermes_tools_a2a.py`, then replace the owning idle MCP runtime. No plugin or
configuration change is required. `test_integration.py` covers cross-origin and
HTTP-downgrade cards through a real local TLS peer and MCP wire, checks error
text/output-schema preservation, and checks the quoted-JSON success case.
Rollback restores the wrapper backup and reloads the idle MCP runtime; doing so
restores the old false-success status for these registry errors.

## First-turn availability after a Gateway restart

A successful standalone `tools/list` does not prove that the first Codex model
turn waited for the server. `a2a-https-hotfix` 1.0.2 uses Codex's native required
MCP setting for Hermes-created clients, without rewriting the shared config.
See the [startup fix and QQ acceptance](../../docs/validation/a2a-windows-first-turn-2026-10-15.md).
