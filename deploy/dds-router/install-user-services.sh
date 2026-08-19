#!/usr/bin/env bash
set -euo pipefail

role="${1:-}"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
target="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$target"

case "$role" in
  orin)
    install -m 0644 "$root/deploy/dds-router/systemd/project-link-dds-router-orin.service" "$target/"
    systemd-analyze --user verify "$target/project-link-dds-router-orin.service"
    ;;
  ubuntu)
    install -m 0644 "$root/deploy/dds-router/systemd/project-link-dds-tunnel.service" "$target/"
    install -m 0644 "$root/deploy/dds-router/systemd/project-link-dds-router-ubuntu.service" "$target/"
    systemd-analyze --user verify \
      "$target/project-link-dds-tunnel.service" \
      "$target/project-link-dds-router-ubuntu.service"
    ;;
  *) echo "Usage: $0 orin|ubuntu" >&2; exit 2 ;;
esac

systemctl --user daemon-reload
echo "DDS transport units installed for $role. They were not enabled or started."
