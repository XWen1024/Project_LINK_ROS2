# Project LINK System Overview

Status: current architecture summary
Last reviewed: 2026-08-15
Canonical branch: `main`

Project LINK is a ROS 2 eldercare mobile-manipulation prototype. The Jetson Orin
Nano owns hardware access and robot control. The Ubuntu 22.04 laptop owns all
operator rendering and GUI interaction.

```text
Ubuntu laptop
  PySide6 console, 2D map rendering, video rendering, RViz2 diagnostics
        |
        | ROS_DOMAIN_ID=42, ROS_LOCALHOST_ONLY=0
        v
Jetson Orin Nano
  base + lidar + Point-LIO + slam_toolbox + Nav2
  voice backend (classic or Qwen, never both)
  YOLO World + SO-101 + VL53L0X
  UWB ingestion and guarded Nav2 bridge
        |
        v
chassis, Unitree L1, cameras, audio, SO-101, BU04, ESP32-C3
```

## Operator Access

Interactive access uses the stable aliases `ssh orin` and `ssh seewo`, while each
alias points to the currently verified numeric DHCP address. On 2026-08-19 the
addresses are Orin `10.255.176.119` and Ubuntu `10.255.176.106`. If an alias stops
connecting after a network change, scan the current bounded subnet and use mDNS
only for candidate discovery. Revalidate hostname, machine-id and the SSH host-key
fingerprint before updating the alias IP; do not depend on a permanent mDNS proxy.

Production motion has one authority chain: an operator or approved module sends
a Nav2 Action goal, and Nav2 owns `/cmd_vel`. Direct-drive and teleoperation are
explicit supervised modes and must not overlap Nav2. The LLM never owns a ROS
publisher, Action client, or arm torque control path.

Current module truth lives in `docs/modules/*/HANDOFF.md`. Hardware experiments
that are not production dependencies live under `docs/archive/experiments/`.
