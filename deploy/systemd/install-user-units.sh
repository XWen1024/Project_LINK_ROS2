#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source_dir="$root/deploy/systemd/user"
target_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
start_agent=0
enable_agent=1

usage() {
  cat <<'EOF'
Usage: deploy/systemd/install-user-units.sh [options]

Install the versioned Project LINK systemd user units. This command never starts
the base, lidar, mapping, Nav2, manipulator, voice, or UWB services.

Options:
  --start-agent      Start/restart the headless console agent after installation.
  --no-enable-agent  Do not enable the console agent for future user sessions.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --start-agent) start_agent=1 ;;
    --no-enable-agent) enable_agent=0 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

mkdir -p "$target_dir"
for unit in "$source_dir"/*.service "$source_dir"/*.target; do
  install -m 0644 "$unit" "$target_dir/$(basename "$unit")"
done
chmod 0755 "$root/deploy/systemd/bin/project-link-component"
chmod 0755 "$root/deploy/systemd/bin/project-link-wait"
chmod 0755 "$root/deploy/systemd/bin/project-link-zero-velocity"

systemd-analyze --user verify "$target_dir"/project-link-*.service "$target_dir"/project-link-*.target
systemctl --user daemon-reload
if [[ "$enable_agent" -eq 1 ]]; then
  systemctl --user enable project-link-console-agent.service
fi
if [[ "$start_agent" -eq 1 ]]; then
  systemctl --user restart project-link-console-agent.service
fi

echo "Installed Project LINK user units in $target_dir"
echo "No hardware or robot stack was started."
