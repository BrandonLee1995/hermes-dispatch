#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <hermes-data-dir> [plugin ...]" >&2
  exit 2
fi

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
target_root="$1"
target_plugins="${target_root}/plugins"
shift

plugins=(codex-app-server-phase-hotfix qqbot-connect-hotfix message-snapshot-store whatsapp-bridge-policy-hotfix)
if [[ $# -gt 0 ]]; then
  plugins=("$@")
fi

mkdir -p "$target_plugins"

for plugin in "${plugins[@]}"; do
  source_dir="${repo_root}/plugins/${plugin}"
  if [[ ! -d "$source_dir" ]]; then
    echo "unknown plugin: ${plugin}" >&2
    exit 2
  fi
  target_dir="${target_plugins}/${plugin}"
  mkdir -p "$target_dir"
  find "$target_dir" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
  cp -R "${source_dir}/." "$target_dir/"
done

echo "installed plugins into ${target_plugins}"
