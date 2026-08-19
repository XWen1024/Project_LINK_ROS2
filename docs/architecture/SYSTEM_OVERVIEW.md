# Project LINK System Overview

Status: current architecture summary
Last reviewed: 2026-08-19
Canonical branch: `main`

Project LINK is a ROS 2 eldercare mobile-manipulation prototype. The Jetson Orin
Nano owns hardware access and robot control. The Ubuntu 22.04 laptop owns all
operator rendering and GUI interaction.

```text
Ubuntu laptop: PySide6 console and ROS domain 142
        |
Ubuntu DDS Router -> TCP 127.0.0.1:11666
        |
SSH LocalForward (authentication, encryption, reconnect)
        |
Orin DDS Router <- TCP 127.0.0.1:11666
        v
Jetson Orin Nano: ROS domain 42
  base + lidar + Point-LIO + slam_toolbox + Nav2
  voice backend (classic or Qwen, never both)
  YOLO World + SO-101 + VL53L0X
  UWB ingestion and guarded Nav2 bridge
        |
        v
chassis, Unitree L1, front camera, arm camera, audio, SO-101, BU04, ESP32-C3
```

Orin publishes the chassis-front camera as `/front_camera/image/compressed` and
the independent arm camera as `/visual_grasp/image/compressed`. Ubuntu only
decodes and renders these streams; it never opens either V4L2 device.

Router participants are restricted with `whitelist-interfaces` to loopback. The
Orin listener was verified as `127.0.0.1:11666`, not `0.0.0.0`. UWB and raw
high-bandwidth Point-LIO/lidar streams are excluded from the MVP allowlist.

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
