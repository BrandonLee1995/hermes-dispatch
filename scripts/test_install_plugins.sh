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

# A profile-level backup root must be a real directory. Following a symlink
# into the recursive plugin discovery tree would make the preserved manifest
# load as a second active plugin.
backup_symlink_root="${test_root}/backup-symlink"
backup_symlink_active="${backup_symlink_root}/plugins/${plugin}"
mkdir -p "$backup_symlink_active"
mkdir -p "${backup_symlink_root}/plugins/escaped-backups"
printf '%s\n' 'name: qqbot-connect-hotfix' 'version: 1.8.7' > \
  "${backup_symlink_active}/plugin.yaml"
printf '%s\n' 'unchanged' > "${backup_symlink_active}/marker.txt"
ln -s plugins/escaped-backups "${backup_symlink_root}/plugin-backups"
if "${repo_root}/scripts/install-plugins.sh" \
  "$backup_symlink_root" "$plugin"; then
  echo "install unexpectedly accepted a symlinked backup root" >&2
  exit 1
fi
[[ -f "${backup_symlink_active}/marker.txt" ]]
[[ -z "$(find "${backup_symlink_root}/plugins/escaped-backups" -name plugin.yaml -print -quit)" ]]

fresh_backup_symlink_root="${test_root}/fresh-backup-symlink"
mkdir -p "${fresh_backup_symlink_root}/plugins/escaped-backups"
ln -s plugins/escaped-backups \
  "${fresh_backup_symlink_root}/plugin-backups"
if "${repo_root}/scripts/install-plugins.sh" \
  "$fresh_backup_symlink_root" "$plugin"; then
  echo "fresh install unexpectedly accepted a symlinked backup root" >&2
  exit 1
fi
[[ -z "$(find "${fresh_backup_symlink_root}/plugins/escaped-backups" -name plugin.yaml -print -quit)" ]]
if [[ -e "${fresh_backup_symlink_root}/plugins/${plugin}" ]]; then
  echo "rejected fresh install unexpectedly changed the active layout" >&2
  exit 1
fi

# The active plugin must be a real direct child of the canonical plugin root.
# Installing through a symlink would mutate an external directory and preserve
# stale files that are absent from the repository copy.
active_symlink_root="${test_root}/active-symlink"
active_symlink_external="${test_root}/active-symlink-external"
mkdir -p "${active_symlink_root}/plugins" "$active_symlink_external"
printf '%s\n' 'name: qqbot-connect-hotfix' 'version: 1.8.7' > \
  "${active_symlink_external}/plugin.yaml"
printf '%s\n' 'unchanged' > "${active_symlink_external}/marker.txt"
ln -s "$active_symlink_external" \
  "${active_symlink_root}/plugins/${plugin}"
if "${repo_root}/scripts/install-plugins.sh" \
  "$active_symlink_root" "$plugin"; then
  echo "install unexpectedly accepted a symlinked active plugin" >&2
  exit 1
fi
grep -q '^version: 1.8.7$' "${active_symlink_external}/plugin.yaml"
[[ -f "${active_symlink_external}/marker.txt" ]]

# Restore applies the same direct-child invariant before backing up or clearing
# an active plugin target.
restore_symlink_root="${test_root}/restore-active-symlink"
restore_symlink_external="${test_root}/restore-active-external"
restore_symlink_backup="${test_root}/restore-active-backup"
mkdir -p "${restore_symlink_root}/plugins"
mkdir -p "$restore_symlink_external" "$restore_symlink_backup"
printf '%s\n' 'name: qqbot-connect-hotfix' 'version: 1.8.8' > \
  "${restore_symlink_external}/plugin.yaml"
printf '%s\n' 'unchanged' > "${restore_symlink_external}/marker.txt"
printf '%s\n' 'name: qqbot-connect-hotfix' 'version: 1.8.7' > \
  "${restore_symlink_backup}/plugin.yaml"
ln -s "$restore_symlink_external" \
  "${restore_symlink_root}/plugins/${plugin}"
if "${repo_root}/scripts/install-plugins.sh" --restore \
  "$restore_symlink_root" "$plugin" "$restore_symlink_backup"; then
  echo "restore unexpectedly accepted a symlinked active plugin" >&2
  exit 1
fi
grep -q '^version: 1.8.8$' "${restore_symlink_external}/plugin.yaml"
[[ -f "${restore_symlink_external}/marker.txt" ]]

"${repo_root}/scripts/install-plugins.sh" --restore \
  "$test_root" "$plugin" "$backup_dir"
restored_version="$(awk '$1 == "version:" {print $2; exit}' "${active_dir}/plugin.yaml")"
[[ "$restored_version" == "1.8.7" ]]
[[ -f "${active_dir}/old-marker.txt" ]]

# Dot path components are not plugin names. They must be rejected before a
# restore target is resolved, backed up, or cleared.
for dot_plugin in . ..; do
  dot_root="${test_root}/dot-${dot_plugin//./dot}"
  dot_backup="${test_root}/dot-backup-${dot_plugin//./dot}"
  mkdir -p "${dot_root}/plugins/safe-plugin" "$dot_backup"
  printf '%s\n' 'unchanged' > "${dot_root}/plugins/safe-plugin/marker.txt"
  printf '%s\n' "name: ${dot_plugin}" 'version: 1.8.7' > \
    "${dot_backup}/plugin.yaml"
  if "${repo_root}/scripts/install-plugins.sh" --restore \
    "$dot_root" "$dot_plugin" "$dot_backup"; then
    echo "restore unexpectedly accepted plugin name ${dot_plugin}" >&2
    exit 1
  fi
  [[ -f "${dot_root}/plugins/safe-plugin/marker.txt" ]]
done
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
echo "plugin_install_backup_root_symlink_guard=ok"
echo "plugin_install_rejected_fresh_layout_unchanged=ok"
echo "plugin_install_active_symlink_guard=ok"
echo "plugin_restore_active_symlink_guard=ok"
echo "plugin_install_restore=ok"
echo "plugin_install_restore_discovery_guard=ok"
echo "plugin_restore_dot_component_guard=ok"
echo "plugin_install_fresh_no_empty_backup=ok"
