#!/usr/bin/env bash
set -euo pipefail

WORKSPACE="${PROJECT_LINK_WORKSPACE:-/home/wte/wheeltec_robot}"
RULE_SOURCE="$WORKSPACE/config/udev/99-project-link-voice.rules"
RULE_TARGET="/etc/udev/rules.d/99-project-link-voice.rules"
WAKE_BY_ID="/dev/serial/by-id/usb-WCH.CN_USB_Single_Serial_0004-if00"

sudo install -m 0644 "$RULE_SOURCE" "$RULE_TARGET"
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=tty --action=add

for _attempt in {1..20}; do
  [[ -e /dev/project_link_wakeup ]] && break
  sleep 0.1
done

if [[ ! -e "$WAKE_BY_ID" ]]; then
  echo "iFlytek wake board is not connected at $WAKE_BY_ID" >&2
  exit 1
fi
if [[ ! -e /dev/project_link_wakeup ]]; then
  echo "udev rule installed, but the alias is absent. Unplug/replug the wake board once." >&2
  exit 1
fi

echo "Stable wake alias installed:"
ls -l /dev/project_link_wakeup "$WAKE_BY_ID"

# shellcheck disable=SC1091
source "$WORKSPACE/scripts/project_link_voice_io.sh"

echo
echo "Stable voice USB binding is ready."
