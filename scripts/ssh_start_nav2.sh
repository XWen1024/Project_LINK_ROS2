#!/usr/bin/env bash
set -euo pipefail
target="${PROJECT_LINK_ORIN_SSH_TARGET:-wte@ubuntu.local}"
exec ssh -o BatchMode=yes -o ConnectTimeout=8 "$target" \
  /home/wte/wheeltec_robot/scripts/standalone/start_nav2.sh
