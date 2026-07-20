# Codex App-Server Compatibility Hotfix

Persistent Hermes plugin for two Hermes 0.18.2 Codex app-server projection
gaps:

1. A completed Codex `final_answer` is also emitted through the gateway
   interim-message callback, which duplicates the final channel reply.
2. Codex's built-in image generator completes as an `imageGeneration` item
   containing base64 bytes. Hermes treats that item as an opaque, truncated
   assistant note. When the turn has no text final answer, the gateway returns
   before its normal media-result scan, so the generated image is never sent.

The plugin forwards explicit `commentary`, suppresses explicit `final_answer`,
and defers unknown-phase messages until later turn activity proves they are
interim. The Codex session projector remains authoritative for final text.

For completed `imageGeneration` items, the plugin validates and atomically
writes the image to:

```text
$HERMES_HOME/cache/images/codex_<item-id>.<ext>
```

It then projects a standard `image_generate` tool result. If the Codex turn has
no final text, it returns `MEDIA:<path>` so the existing Hermes adapter sends
the file as a native image. Set `HERMES_CODEX_IMAGE_CACHE_DIR` only when the
gateway's allowed media roots include that directory. Encoded results above
80 MiB and unsupported file signatures are rejected.

Install under the Hermes data directory and enable it:

```bash
scripts/install-plugins.sh "$HOME/.hermes"
hermes plugins enable codex-app-server-phase-hotfix
```

Restart the gateway after installation. Verify both fixes with:

```bash
python plugins/codex-app-server-phase-hotfix/test_hotfix.py
```

To roll back, disable the plugin and restart Hermes. Existing generated cache
files are not deleted automatically:

```bash
hermes plugins disable codex-app-server-phase-hotfix
hermes gateway restart
```

The phase portion behaviorally detects an equivalent upstream fix and becomes a
no-op. Remove the image portion once upstream projects `imageGeneration` into a
normal deliverable media result and handles media-only turns before its empty
response return.
