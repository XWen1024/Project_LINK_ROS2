# Project LINK System Overview

Status: current architecture summary
Last reviewed: 2026-08-19
Canonical branch: `main`

Project LINK is a ROS 2 eldercare mobile-manipulation prototype. The Jetson Orin
Nano owns hardware access and robot control. The Ubuntu 22.04 laptop owns all
operator rendering and GUI interaction.

```text
Ubuntu laptop: PySide6 console, ROS domain 42 for the MVP
        | native DDS Peer for ROS Topic / Service / Action
        | SSH for lifecycle, configuration and secrets
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

The Android fall guard talks directly to the Orin's authenticated LAN HTTP
gateway. Its first production gate is static and no-motion: SQLite event state,
the shared `/front_camera/capture_still` service, local YOLO pose selection,
SiliconFlow VLM and a single bound WeChat contact. This stack does not start
Nav2 and never publishes `/cmd_vel`.

The source-locked DDS Router experiment remains under `deploy/dds-router/` but is
not production-enabled. On 2026-08-19 both binaries, loopback listeners, SSH
forwarding and typed endpoint probes were verified individually, yet no ROS
endpoint crossed the WAN participants. The MVP therefore keeps the already
working native DDS Peer path. The planned robust replacement is a typed,
allowlisted console bridge over SSH with explicit reconnect/state resync.

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
