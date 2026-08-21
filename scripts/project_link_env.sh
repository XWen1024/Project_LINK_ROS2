#!/usr/bin/env bash

export ROS_DOMAIN_ID=42
export ROS_LOCALHOST_ONLY=0

# Jetson USB device mode is the preferred field data link. Override stale
# console.env Wi-Fi values whenever the Ubuntu console peer is physically live.
usb_console_ip="${PROJECT_LINK_USB_CONSOLE_IP:-192.168.55.100}"
if [ "${PROJECT_LINK_PREFER_USB_DIRECT:-1}" = "1" ] && \
   ip -o -4 addr show dev l4tbr0 2>/dev/null | grep -q '192\.168\.55\.1/' && \
   ping -I l4tbr0 -c 1 -W 1 "$usb_console_ip" >/dev/null 2>&1; then
  export PROJECT_LINK_DDS_INTERFACE=l4tbr0
  export PROJECT_LINK_DDS_PEER_IP="$usb_console_ip"
  export PROJECT_LINK_ENABLE_SINGLE_INTERFACE_DDS=1
  export PROJECT_LINK_TRANSPORT_MODE=usb-direct
fi

if [ "${PROJECT_LINK_ENABLE_SINGLE_INTERFACE_DDS:-0}" = "1" ] && \
   [ -f /home/wte/wheeltec_robot/scripts/project_link_dds_profile.sh ]; then
  source /home/wte/wheeltec_robot/scripts/project_link_dds_profile.sh
fi
source /opt/ros/humble/setup.bash
if [ -f /home/wte/point_lio_ws/install/setup.bash ]; then
  source /home/wte/point_lio_ws/install/setup.bash
fi
source /home/wte/wheeltec_robot/install/setup.bash
