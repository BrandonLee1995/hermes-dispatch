# AGENTS.md

## Repository Purpose

This repository carries removable compatibility layers for Hermes official
containers. Fixes should live in host-mounted, persistent paths and should be
easy to remove when upstream Hermes catches up.

## Compatibility Rules

- Before modifying platform behavior, read the relevant upstream Hermes source
  and official platform API documentation when the behavior depends on an
  external API contract.
- Do not edit source files inside a running `hermes-agent` container as the
  durable fix.
- Apply changes through mounted paths such as `/opt/data/plugins`,
  `/opt/data/scripts`, config files, or deployment templates.
- Keep each workaround documented with the upstream behavior or platform
  requirement it compensates for.
- Never commit real tokens, phone numbers, QQ/WhatsApp identifiers, Cloudflare
  tunnel tokens, or dashboard/API credentials.

## Permission Boundary

- Department-owned data must be written as the department Linux user whenever
  the operation touches mounted Hermes data.
- Avoid creating `root:root` files under mounted Hermes data trees.
- When a container command may write `/opt/data`, prefer `docker exec -u hermes`
  unless orchestration itself requires root.
