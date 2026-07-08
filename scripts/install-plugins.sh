#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <hermes-data-dir>" >&2
  exit 2
fi

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
target_root="$1"
target_plugins="${target_root}/plugins"

mkdir -p "$target_plugins"

for plugin in qqbot-connect-hotfix whatsapp-bridge-policy-hotfix; do
  source_dir="${repo_root}/plugins/${plugin}"
  target_dir="${target_plugins}/${plugin}"
  mkdir -p "$target_dir"
  find "$target_dir" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
  cp -R "${source_dir}/." "$target_dir/"
done

echo "installed plugins into ${target_plugins}"
