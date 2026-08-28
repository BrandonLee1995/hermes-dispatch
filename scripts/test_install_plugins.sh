#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
test_root="$(mktemp -d "${TMPDIR:-/tmp}/hermes-plugin-install-test.XXXXXXXX")"
trap 'rm -rf "$test_root"' EXIT

plugin="qqbot-connect-hotfix"
active_dir="${test_root}/plugins/${plugin}"
mkdir -p "$active_dir"
printf '%s\n' 'name: qqbot-connect-hotfix' 'version: 1.8.7' > "${active_dir}/plugin.yaml"
printf '%s\n' 'preserve-me' > "${active_dir}/old-marker.txt"
printf '%s\n' 'preserve-hidden' > "${active_dir}/.old-hidden"

"${repo_root}/scripts/install-plugins.sh" "$test_root" "$plugin"

backup_root="${test_root}/plugin-backups"
backup_count="$(find "$backup_root" -mindepth 1 -maxdepth 1 -type d -name "${plugin}-1.8.7-*" | wc -l | tr -d ' ')"
[[ "$backup_count" == "1" ]]
backup_dir="$(find "$backup_root" -mindepth 1 -maxdepth 1 -type d -name "${plugin}-1.8.7-*" -print -quit)"
[[ -f "${backup_dir}/old-marker.txt" ]]
[[ -f "${backup_dir}/.old-hidden" ]]
[[ ! -e "${active_dir}/old-marker.txt" ]]
[[ ! -d "${test_root}/plugins/.backups" ]]

source_version="$(awk '$1 == "version:" {print $2; exit}' "${repo_root}/plugins/${plugin}/plugin.yaml")"
active_version="$(awk '$1 == "version:" {print $2; exit}' "${active_dir}/plugin.yaml")"
[[ "$active_version" == "$source_version" ]]

"${repo_root}/scripts/install-plugins.sh" --restore \
  "$test_root" "$plugin" "$backup_dir"
restored_version="$(awk '$1 == "version:" {print $2; exit}' "${active_dir}/plugin.yaml")"
[[ "$restored_version" == "1.8.7" ]]
[[ -f "${active_dir}/old-marker.txt" ]]
[[ -f "${active_dir}/.old-hidden" ]]

# A restore source inside plugin discovery could load as a second plugin and
# must be rejected before the active directory changes.
unsafe_backup="${test_root}/plugins/unsafe-backup"
mkdir -p "$unsafe_backup"
cp -R "${backup_dir}/." "$unsafe_backup/"
if "${repo_root}/scripts/install-plugins.sh" --restore \
  "$test_root" "$plugin" "$unsafe_backup"; then
  echo "restore unexpectedly accepted a backup inside plugin discovery" >&2
  exit 1
fi
[[ -f "${active_dir}/old-marker.txt" ]]

# A first installation has no prior directory to preserve and must not create
# an empty backup artifact.
fresh_root="${test_root}/fresh"
"${repo_root}/scripts/install-plugins.sh" "$fresh_root" "$plugin"
[[ ! -e "${fresh_root}/plugin-backups" ]]

echo "plugin_install_external_backup=ok"
echo "plugin_install_restore=ok"
echo "plugin_install_restore_discovery_guard=ok"
echo "plugin_install_fresh_no_empty_backup=ok"
