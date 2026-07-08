# WhatsApp Bridge Policy Hotfix

Local Hermes WhatsApp bridge compatibility hotfix.

This plugin is designed for mounted, persistent deployment under:

```text
/opt/data/plugins/whatsapp-bridge-policy-hotfix
```

It avoids editing the running image as the durable state. On gateway startup it
copies the image's current `scripts/whatsapp-bridge` files into a runtime folder
under this plugin, patches only `bridge.js`, and points `WhatsAppAdapter` at that
runtime script.

Compatibility behavior:

- `WHATSAPP_ALLOWED_USERS` remains a DM sender allowlist.
- Group messages are no longer filtered by `WHATSAPP_ALLOWED_USERS` in the Node
  bridge.
- Group access is delegated to the Python adapter's `group_policy` and
  `group_allow_from` / `WHATSAPP_GROUP_ALLOWED_USERS`.
- Slash-command admin gates can be configured from dashboard-visible env vars:
  `WHATSAPP_ALLOW_ADMIN_FROM`, `WHATSAPP_USER_ALLOWED_COMMANDS`,
  `WHATSAPP_GROUP_ALLOW_ADMIN_FROM`, and
  `WHATSAPP_GROUP_USER_ALLOWED_COMMANDS`.

The desired model is:

```text
DM:
  sender must satisfy WHATSAPP_ALLOWED_USERS / allow_from

Group:
  WHATSAPP_GROUP_POLICY=open allows joined groups without per-member allowlisting
  sender does not need to be listed in WHATSAPP_ALLOWED_USERS

Admin slash commands:
  controlled separately by DM/group admin allowlists
```

For the common "DM allowlist, group open" model, set:

```text
WHATSAPP_DM_POLICY=allowlist
WHATSAPP_ALLOWED_USERS=<department-private-chat-users>
WHATSAPP_GROUP_POLICY=open
WHATSAPP_ALLOW_ALL_USERS=true
```

`WHATSAPP_ALLOW_ALL_USERS=true` is required by Hermes' startup guard whenever a
platform policy is `open`. With `WHATSAPP_DM_POLICY=allowlist`, it does not make
private chats open; private-chat intake still requires `WHATSAPP_ALLOWED_USERS`.

Remove this plugin after upstream Hermes implements the same policy split.
