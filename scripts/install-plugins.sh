#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: $0 <hermes-data-dir> [plugin ...]" >&2
  echo "       $0 --restore <hermes-data-dir> <plugin> <backup-dir>" >&2
  exit 2
}

plugin_version() {
  local plugin_dir="$1"
  local version
  version="$(awk '$1 == "version:" {print $2; exit}' "${plugin_dir}/plugin.yaml" 2>/dev/null || true)"
  if [[ ! "$version" =~ ^[0-9A-Za-z._-]+$ ]]; then
    version="unknown"
  fi
  printf '%s\n' "$version"
}

backup_existing_plugin() {
  local data_root="$1"
  local plugin="$2"
  local active_dir="$3"
  local existing_version backup_root timestamp backup_dir suffix

  if [[ ! -d "$active_dir" ]] || [[ -z "$(find "$active_dir" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
    return
  fi

  existing_version="$(plugin_version "$active_dir")"
  backup_root="${data_root}/plugin-backups"
  timestamp="$(date -u +%Y%m%d-%H%M%S)"
  backup_dir="${backup_root}/${plugin}-${existing_version}-${timestamp}"
  suffix=1
  mkdir -p "$backup_root"
  while [[ -e "$backup_dir" ]]; do
    backup_dir="${backup_root}/${plugin}-${existing_version}-${timestamp}-${suffix}"
    suffix=$((suffix + 1))
  done
  mkdir "$backup_dir"
  cp -R "${active_dir}/." "$backup_dir/"
  echo "backed up ${plugin} to ${backup_dir}"
}

validate_plugin_name() {
  local plugin="$1"
  if [[ ! "$plugin" =~ ^[0-9A-Za-z._-]+$ ]]; then
    echo "invalid plugin name: ${plugin}" >&2
    exit 2
  fi
}

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ "${1:-}" == "--restore" ]]; then
  [[ $# -eq 4 ]] || usage
  target_root="$2"
  plugin="$3"
  backup_dir="$4"
  validate_plugin_name "$plugin"
  [[ "$target_root" != "/" ]] || usage
  [[ -d "$backup_dir" && -f "${backup_dir}/plugin.yaml" ]] || {
    echo "invalid plugin backup: ${backup_dir}" >&2
    exit 2
  }

  mkdir -p "$target_root"
  target_root="$(cd -- "$target_root" && pwd -P)"
  [[ "$target_root" != "/" ]] || usage
  mkdir -p "$target_root/plugins"
  target_plugins="$(cd -- "$target_root/plugins" && pwd -P)"
  backup_dir="$(cd -- "$backup_dir" && pwd -P)"
  case "${backup_dir}/" in
    "${target_plugins}/"*)
      echo "plugin backup must be outside discovery root: ${target_plugins}" >&2
      exit 2
      ;;
  esac

  backup_name="$(awk '$1 == "name:" {print $2; exit}' "${backup_dir}/plugin.yaml")"
  if [[ "$backup_name" != "$plugin" ]]; then
    echo "backup contains ${backup_name:-unknown}, not ${plugin}" >&2
    exit 2
  fi

  target_dir="${target_plugins}/${plugin}"
  backup_existing_plugin "$target_root" "$plugin" "$target_dir"
  mkdir -p "$target_dir"
  find "$target_dir" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
  cp -R "${backup_dir}/." "$target_dir/"
  echo "restored ${plugin} from ${backup_dir}"
  exit 0
fi

[[ $# -ge 1 ]] || usage
target_root="$1"
[[ "$target_root" != "/" ]] || usage
shift

plugins=(codex-app-server-phase-hotfix qqbot-connect-hotfix message-snapshot-store whatsapp-bridge-policy-hotfix)
if [[ $# -gt 0 ]]; then
  plugins=("$@")
fi

mkdir -p "$target_root"
target_root="$(cd -- "$target_root" && pwd -P)"
[[ "$target_root" != "/" ]] || usage
mkdir -p "$target_root/plugins"
target_plugins="$(cd -- "$target_root/plugins" && pwd -P)"

for plugin in "${plugins[@]}"; do
  validate_plugin_name "$plugin"
  source_dir="${repo_root}/plugins/${plugin}"
  if [[ ! -d "$source_dir" || ! -f "${source_dir}/plugin.yaml" ]]; then
    echo "unknown plugin: ${plugin}" >&2
    exit 2
  fi
done

for plugin in "${plugins[@]}"; do
  source_dir="${repo_root}/plugins/${plugin}"
  target_dir="${target_plugins}/${plugin}"
  backup_existing_plugin "$target_root" "$plugin" "$target_dir"
  mkdir -p "$target_dir"
  find "$target_dir" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
  cp -R "${source_dir}/." "$target_dir/"
done

echo "installed plugins into ${target_plugins}"
