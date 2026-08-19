#!/usr/bin/env bash
set -euo pipefail

declare -A expected=(
  [/dev/project_link_chassis]="1a86:55d4:5B1F024697"
  [/dev/project_link_lidar]="10c4:ea60:02AF3DE8"
  [/dev/project_link_so101]="1a86:55d3:5B3E119094"
  [/dev/project_link_wakeup]="1a86:55d4:0004"
  [/dev/project_link_front_camera]="2993:0858:20240307110322"
  [/dev/project_link_arm_camera]="0bda:5844:200901010001"
)

failed=0
for alias in "${!expected[@]}"; do
  if [[ ! -e "$alias" ]]; then
    echo "MISSING $alias"
    failed=1
    continue
  fi
  properties="$(udevadm info -q property -n "$alias")"
  vendor="$(grep '^ID_VENDOR_ID=' <<<"$properties" | cut -d= -f2)"
  product="$(grep '^ID_MODEL_ID=' <<<"$properties" | cut -d= -f2)"
  serial="$(grep '^ID_SERIAL_SHORT=' <<<"$properties" | cut -d= -f2)"
  actual="$vendor:$product:$serial"
  if [[ "$actual" != "${expected[$alias]}" ]]; then
    echo "MISMATCH $alias expected=${expected[$alias]} actual=$actual"
    failed=1
  else
    echo "OK $alias -> $(readlink -f "$alias") ($actual)"
  fi
done

if [[ -e /dev/project_link_chassis && -e /dev/project_link_wakeup ]] && \
   [[ "$(readlink -f /dev/project_link_chassis)" == "$(readlink -f /dev/project_link_wakeup)" ]]; then
  echo "MISMATCH chassis and wake aliases resolve to one device"
  failed=1
fi

exit "$failed"
