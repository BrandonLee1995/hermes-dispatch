# HTTPS-only A2A

Hermes 0.21.0's bundled A2A adapter uses `ThreadingHTTPServer` without TLS.
Its clients also accept HTTP endpoints and redirects. This removable plugin
registers the same upstream A2A platform and tools with a TLS transport policy.
It does not implement a second protocol, credential store, or task dispatcher.

## Behavior and boundary

- The A2A listener requires a certificate, key, and HTTPS `A2A_PUBLIC_URL` before
  binding. It speaks TLS directly; there is no plaintext backend hop.
- TLS 1.2 is the minimum. Handshakes run in connection workers with a timeout.
- Discovery, task calls, and outbound push requests require HTTPS. Redirects
  are refused; an Agent Card cannot move the credential-bearing RPC request
  to a different origin. Certificate-chain and hostname checks remain enabled.
- An optional private CA is added to system trust for this A2A client only.
  Other plugins' HTTP clients and the machine's trust store are untouched.
- Authentication, trusted peers, rate limits, task routing, and conversation
  storage remain upstream-owned. Keep using separate per-peer bearer tokens.

Verified against Hermes `0.21.0` / `29112bef09`. This uses internal adapter
boundaries, so rerun its tests when upgrading. References:
[upstream A2A](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/a2a),
[Python TLS sockets](https://docs.python.org/3/library/ssl.html#ssl.SSLContext.wrap_socket).

## Enable

Install as the profile's OS owner:

```bash
scripts/install-plugins.sh "$HERMES_HOME" a2a-https-hotfix
```

Merge the following into the profile configuration, preserving other plugins
and settings. **Disable the bundled `a2a-platform`**: this plugin replaces its
registration so a failed/missing TLS plugin cannot fall back to plaintext.
Do not enable both implementations.

```yaml
plugins:
  enabled: [a2a-https-hotfix]  # append to the existing list
  disabled: [a2a-platform]    # append to the existing list
  entries:
    a2a-https-hotfix:
      settings:
        cert_file: /absolute/private/a2a/server.pem
        key_file: /absolute/private/a2a/server.key
        ca_file: /absolute/private/a2a/ca.pem  # omit for a publicly trusted CA
        path_prefix: default  # optional single segment; /default on this profile
platforms:
  a2a:
    enabled: true
display:
  platforms:
    a2a:
      streaming: false
      interim_assistant_messages: false
```

Keep streaming and interim messages disabled **only for A2A**. Hermes 0.21.0's
Gateway streaming consumer can consider a preview delivered even though A2A
ignores non-final sends; the final task result then contains only the remaining
suffix (observed as `Product received ...` becoming `received ...`). Buffering
the complete final response through the upstream per-platform display settings
avoids that loss without changing QQ/WhatsApp streaming or credentials.

Set `A2A_HOST`, a unique `A2A_PORT`, `A2A_AGENT_NAME`, `A2A_PUBLIC_URL` (HTTPS),
`A2A_PEER_TOKENS`, and `A2A_TRUSTED_PEERS` in that profile's runtime `.env`.
Store real credentials only there / in runtime configuration, never in this
repository. Keep private keys and secret-bearing config/backups owner-only.
With multiple profiles on one host, each needs its own port and identity.
Set `A2A_PUBLIC_URL` to the HTTPS origin without the path; `path_prefix` adds
the profile path to both routing and the Agent Card. It also supports `default`,
which the upstream served-agent slug configuration reserves. This does not
forward into another profile; the request still uses this Gateway's local agent.

Peer URLs in `a2a_agents` must be HTTPS and match the server certificate's SAN.
Tailscale's existing full node DNS name can be used with a profile-specific
port/path. Arbitrary profile subdomains do not automatically become MagicDNS
records. Other machines must trust the private CA if one is used; distribute
only its public certificate, never its signing key. Tailscale connectivity
does not replace TLS certificate verification.

Drain and restart only the affected Gateways. Fresh logs from upstream may
still label its adapter `a2a-http` / `http://` because its logging text is not
rewritten; verify actual sockets with TLS, not those labels.

For Codex runtime callers also install the
[hermes-tools A2A entrypoint](../../mcp/hermes-tools-a2a/README.md), including
`env_vars = ["HERMES_HOME"]`. Profile discovery loads this plugin in that MCP
process and applies the same outbound policy. Configuration and credentials
are still resolved under the caller's home.

## Verify

```bash
/absolute/hermes/venv/bin/python plugins/a2a-https-hotfix/test_https.py -v
CODEX_BINARY=/absolute/path/to/codex HERMES_SOURCE_DIR=/absolute/hermes/source \
  /absolute/hermes/venv/bin/python mcp/hermes-tools-a2a/test_integration.py -v
```

Tests use disposable certificates (via Hermes' installed `cryptography`
dependency), isolated profiles, and local TLS peers. Check HTTPS success,
untrusted CA / hostname rejection, HTTP / redirect rejection, profile isolation,
and the actual app-server -> MCP -> TLS request path. Production acceptance must
add a real QQ/WhatsApp inbound request and the original-channel reply, with
the target profile's A2A transcript as evidence.

## Rollback

Back up `.env`, `config.yaml`, the owning Codex MCP configuration, and existing
plugin/script files before installation. Restore only the fields/files changed
by this deployment, checking for newer operator edits first. Drain and restart
the affected Gateway and replace its idle Codex runtime. If HTTPS cannot be
restored, **disable A2A**; never re-enable the bundled plaintext listener as a
fallback. Do not delete credentials, conversation history, or unrelated plugins.
Renew certificates before expiry and restart the idle Gateway to load them.

## Runtime failure status (1.0.1)

Hermes 0.21.0 returns a structured failed/partial/interrupted runtime result,
but the Gateway converts it to a user-visible notice. Upstream A2A `send()` marks
any final notice as completed, so a failed Codex startup can falsely succeed.
`result_status.py` carries the runtime outcome within the current asynchronous
processing task and resolves that exact A2A task as `TASK_STATE_FAILED`, retaining
the failure text. It never infers failure from words in an assistant reply, and
does not resolve a different task queued in the same context.

The shared outbound `_send_task` path treats failed/rejected/canceled terminal
states as tool errors. Input-required and successful states retain their native
semantics. `a2a_call` and orchestration's first/best selection therefore cannot
select a failed terminal task as a success. Native orchestration mode `all` still
returns an aggregate report; inspect each peer's result (partial success is not
an atomic transaction).

Enable by updating this plugin's Python files and metadata, and the
`mcp/hermes-tools-a2a/hermes_tools_a2a.py` startup wrapper, then restarting the idle
Gateway so its Codex/MCP children reload. No credentials, TLS settings or model
defaults need changing. Existing recorded tasks are not rewritten retroactively.
Verify `test_result_status.py` against installed Hermes, run the MCP integration
suite, then check a real failure has both A2A failed status and MCP `isError=true`.
The regression can reproduce the upstream bug with `test_result_status.py --upstream`.

Rollback: restore the backed-up plugin and MCP wrapper files and restart the
idle Gateway. Keep HTTPS enabled; disabling the status fix is not a reason to
re-enable plaintext A2A. The old false-success behavior returns after rollback.

Contracts: [A2A task states](https://a2a-protocol.org/latest/specification/#414-taskstate)
and [MCP tool errors](https://modelcontextprotocol.io/specification/2025-11-25/server/tools#error-handling).

## First-turn Codex MCP readiness (1.0.2)

Codex normally gives optional MCP servers a 1000 ms shared startup grace
period before building the initial tool catalog. On the tested Windows host (Codex 0.159.2),
`hermes-tools` takes about 2.7–3 seconds to initialize. The first QQ model turn
can therefore see no `a2a_call`, even though a later turn sees the same server.
This is separate from A2A networking, bearer authentication and turn limits.

The plugin adds the native command-line override
`-c mcp_servers.hermes-tools.required=true` only to Hermes-created Codex clients
whose resolved main `CODEX_HOME/config.toml` already contains an enabled
`hermes-tools` server. It preserves constructor arguments and environment values,
adds no fixed sleep, and does not rewrite configuration or credentials. Already
required, disabled and absent entries are left alone. Other MCP server startup policies remain
unchanged. Project-only server definitions are outside this deployment's scope.

Codex now waits for this server's actual initialization before the first model
turn. Its existing startup timeout still applies (default 10 seconds); failed
initialization reports a startup failure instead of silently starting a model
without the required tools. This patch does not change `A2A_MAX_PINGPONG_TURNS`;
the cross-host acceptance profiles separately configured it to 20.

Enable by updating `__init__.py`, `mcp_startup.py` and plugin metadata in the
profile's persistent plugin directory, then restarting its idle Gateway. Verify:

- `test_mcp_startup.py`: argument/env/config preservation and disabled/absent gates.
- `test_mcp_first_turn.py` with `CODEX_BINARY` and Hermes `PYTHONPATH`: real Codex,
  a delayed local MCP fixture, and a local Responses fixture execute the first
  code-mode `ALL_TOOLS` lookup. The old path lacks A2A; the patched path has it.
  A failed required server must prevent model startup. No live model is used.
- Restart the target Gateway, send one QQ request for the configured HTTPS peer,
  and verify the first incoming request reaches the peer and returns its complete
  reply without another user message or a separate warm-up call.

Rollback by restoring the backed-up plugin entrypoint/metadata and removing the
new module only if it did not exist before, then restarting the idle Gateway.
Keep the TLS and failure-status layers enabled. This restores the optional-server
race; configuration files and credentials do not need restoration.

Official contracts: [MCP startup options](https://learn.chatgpt.com/docs/extend/mcp),
[required MCP configuration](https://learn.chatgpt.com/docs/config-file/config-reference),
and [app-server inventory/status APIs](https://learn.chatgpt.com/docs/app-server).
