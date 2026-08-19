#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_rule="$root/config/udev/99-project-link-hardware.rules"
target_rule="/etc/udev/rules.d/99-project-link-hardware.rules"
dangerous_rule="/etc/udev/rules.d/wheeltec_controller.rules"

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Run with sudo: sudo $root/scripts/install_project_link_hardware_aliases.sh" >&2
  exit 2
fi

[[ -f "$source_rule" ]] || { echo "Missing $source_rule" >&2; exit 1; }

required_ids=(
  /dev/serial/by-id/usb-1a86_USB_Single_Serial_5B1F024697-if00
  /dev/serial/by-id/usb-Silicon_Labs_CP2104_USB_to_UART_Bridge_Controller_02AF3DE8-if00-port0
  /dev/serial/by-id/usb-1a86_USB_Single_Serial_5B3E119094-if00
  /dev/serial/by-id/usb-WCH.CN_USB_Single_Serial_0004-if00
)
for device in "${required_ids[@]}"; do
  [[ -e "$device" ]] || { echo "Required hardware is missing: $device" >&2; exit 1; }
done

# The vendor-generated rule below matches every 1a86:55d4 device and currently
# aliases the voice wake board as the chassis. Preserve it as a disabled backup.
if [[ -f "$dangerous_rule" ]] && ! grep -q 'ATTRS{serial}' "$dangerous_rule"; then
  backup="${dangerous_rule}.project-link-disabled"
  cp -a "$dangerous_rule" "$backup"
  rm -f "$dangerous_rule"
  echo "Disabled unsafe broad rule; backup: $backup"
fi

install -m 0644 "$source_rule" "$target_rule"
udevadm control --reload-rules
udevadm trigger --subsystem-match=tty
udevadm trigger --subsystem-match=video4linux
udevadm settle

aliases=(
  /dev/project_link_chassis
  /dev/project_link_lidar
  /dev/project_link_so101
  /dev/project_link_wakeup
  /dev/project_link_front_camera
  /dev/project_link_arm_camera
)
for alias in "${aliases[@]}"; do
  [[ -e "$alias" ]] || { echo "Alias was not created: $alias" >&2; exit 1; }
  printf '%-38s -> %s\n' "$alias" "$(readlink -f "$alias")"
done

if [[ "$(readlink -f /dev/project_link_chassis)" == "$(readlink -f /dev/project_link_wakeup)" ]]; then
  echo "Safety failure: chassis and wake aliases resolve to the same device" >&2
  exit 1
fi

echo "Project LINK hardware aliases installed. No service or physical motion was started."
