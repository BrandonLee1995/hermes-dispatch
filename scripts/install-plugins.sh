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

validate_backup_root() {
  local data_root="$1"
  local backup_root="${data_root}/plugin-backups"
  local plugin_root

  if [[ -L "$backup_root" ]]; then
    echo "plugin backup root must not be a symlink: ${backup_root}" >&2
    return 2
  fi
  if [[ -e "$backup_root" && ! -d "$backup_root" ]]; then
    echo "plugin backup root must be a directory: ${backup_root}" >&2
    return 2
  fi
  plugin_root="$(cd -- "${data_root}/plugins" && pwd -P)"
  if [[ -d "$backup_root" ]]; then
    backup_root="$(cd -- "$backup_root" && pwd -P)"
    case "${backup_root}/" in
      "${plugin_root}/"*)
        echo "plugin backup root must be outside discovery root: ${plugin_root}" >&2
        return 2
        ;;
    esac
  fi
}

backup_existing_plugin() {
  local data_root="$1"
  local plugin="$2"
  local active_dir="$3"
  local existing_version backup_root timestamp backup_dir suffix

  backup_root="${data_root}/plugin-backups"
  validate_backup_root "$data_root"

  if [[ ! -d "$active_dir" ]] || [[ -z "$(find "$active_dir" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
    return
  fi

  existing_version="$(plugin_version "$active_dir")"
  mkdir -p "$backup_root"
  validate_backup_root "$data_root"
  backup_root="$(cd -- "$backup_root" && pwd -P)"
  timestamp="$(date -u +%Y%m%d-%H%M%S)"
  backup_dir="${backup_root}/${plugin}-${existing_version}-${timestamp}"
  suffix=1
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
  if [[ "$plugin" == "." || "$plugin" == ".." || ! "$plugin" =~ ^[0-9A-Za-z._-]+$ ]]; then
    echo "invalid plugin name: ${plugin}" >&2
    exit 2
  fi
}

canonical_active_plugin_dir() {
  local plugin_root="$1"
  local plugin="$2"
  local target_dir="${plugin_root}/${plugin}"
  local resolved_target

  if [[ -L "$target_dir" ]]; then
    echo "active plugin target must not be a symlink: ${target_dir}" >&2
    return 2
  fi
  if [[ -e "$target_dir" && ! -d "$target_dir" ]]; then
    echo "active plugin target must be a directory: ${target_dir}" >&2
    return 2
  fi
  if [[ -d "$target_dir" ]]; then
    resolved_target="$(cd -- "$target_dir" && pwd -P)"
  else
    resolved_target="$target_dir"
  fi
  if [[ "$resolved_target" != "${plugin_root}/${plugin}" ]]; then
    echo "active plugin target must be a direct child of ${plugin_root}" >&2
    return 2
  fi
  printf '%s\n' "$resolved_target"
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

  validate_backup_root "$target_root"
  target_dir="$(canonical_active_plugin_dir "$target_plugins" "$plugin")"
  mkdir -p "$target_dir"
  backup_existing_plugin "$target_root" "$plugin" "$target_dir"
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

validate_backup_root "$target_root"
target_dirs=()
for plugin in "${plugins[@]}"; do
  target_dirs+=("$(canonical_active_plugin_dir "$target_plugins" "$plugin")")
done

for ((index = 0; index < ${#plugins[@]}; index++)); do
  plugin="${plugins[$index]}"
  source_dir="${repo_root}/plugins/${plugin}"
  target_dir="${target_dirs[$index]}"
  mkdir -p "$target_dir"
  backup_existing_plugin "$target_root" "$plugin" "$target_dir"
  find "$target_dir" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
  cp -R "${source_dir}/." "$target_dir/"
done

echo "installed plugins into ${target_plugins}"
