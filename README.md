# Project LINK ROS 2

Project LINK / 灵犀 is an eldercare mobile-manipulation prototype built around a
Jetson Orin Nano, a differential Wheeltec base, Unitree L1 lidar, SO-101 arm,
voice tools, BU04 UWB and an ESP32-C3 VL53L0X bridge.

The current product direction is a single Ubuntu 22.04 PySide6 control console.
Ubuntu performs all rendering; Orin remains a headless ROS 2 hardware and control server.

## Current State

- Canonical branch: `main`; no active feature or experiment branches.
- Navigation: supervised Point-LIO Phase B, slam_toolbox and Nav2 stack is the
  primary route; rf2o remains a fallback.
- Manipulation: headless YOLO World + SO-101 + optional ToF with Ubuntu remote GUI.
- Voice: classic Volcano/DeepSeek pipeline and Qwen Realtime are both available
  but must never run simultaneously.
- UWB: code is preserved, but the page is hidden and the module is outside the current MVP.
- Console: typed interfaces, headless agent, versioned systemd user units and the
  PySide6 pages are implemented. The visible Ubuntu GUI includes navigation with
  a distinct chassis-front camera preview, manipulation with its independent arm
  camera, voice switching/timing, voice profiles/tools and masked global settings.
  The window is desktop-bounded, the sidebar can
  collapse to icons, and real mode is single-instance to avoid duplicate ROS
  nodes. Live hardware loops remain supervised field items.
- Transport: DDS Router v2.2.0 is source-locked and verified on Orin ARM64. The
  loopback-only Orin listener and Ubuntu-to-Orin SSH tunnel are working; Ubuntu
  x86_64 Router build and full domain-142 cutover remain.

See [PROGRESS.md](PROGRESS.md) for the active milestone and remaining hardware gates.

## Documentation

- [Documentation index](docs/README.md)
- [System overview](docs/architecture/SYSTEM_OVERVIEW.md)
- [Console architecture](docs/architecture/CONSOLE_ARCHITECTURE.md)
- [Navigation handoff](docs/modules/navigation/HANDOFF.md)
- [Manipulation handoff](docs/modules/manipulation/HANDOFF.md)
- [Voice overview](docs/modules/voice/OVERVIEW.md)
- [UWB handoff](docs/modules/uwb/HANDOFF.md)
- [VL53L0X handoff](docs/modules/sensors/vl53l0x/HANDOFF.md)

The previous detailed README is preserved at
`docs/archive/handoffs/README_DETAIL_20260815.md`.

## Repository Layout

```text
src/                 ROS 2 packages
scripts/             operator and deployment helpers
configs/             repository-owned integration defaults
deploy/              systemd user units and deployment assets
docs/modules/        current module handoffs and runbooks
docs/architecture/   system and console design
docs/archive/        superseded handoffs and experiments
external/            repository-owned firmware or ignored reference checkouts
```

## Build On Orin

```bash
cd /home/wte/wheeltec_robot
source /opt/ros/humble/setup.bash
colcon build
source install/setup.bash
```

Some Humble Python packages on the current Orin must use normal install mode
instead of `--symlink-install` because of the installed setuptools version.

After building the console packages, install the user units without starting
hardware:

```bash
./deploy/systemd/install-user-units.sh
```

See [deploy/systemd/README.md](deploy/systemd/README.md). The systemd route is
not the production default until it passes two supervised field cycles.

The GUI has a hardware-free demo mode:

```bash
ros2 run project_link_console_gui project_link_console --demo
```

See [src/project_link_console_gui/README.md](src/project_link_console_gui/README.md).

## ROS Network

Use the same environment on Orin and the Ubuntu laptop:

```bash
export ROS_DOMAIN_ID=42
export ROS_LOCALHOST_ONLY=0
```

Operator SSH uses `ssh orin` (`wte`) and `ssh seewo` (`xwen`). Their Windows SSH
aliases point to the currently verified numeric IPs; on 2026-08-19 these are
`10.255.176.119` and `10.255.176.106`. When the LAN changes, use mDNS only to
rediscover candidates, verify hostname/machine-id/host-key identity, and update
the numeric alias instead of installing a permanent mDNS proxy mapping.

On Orin prefer:

```bash
source /home/wte/wheeltec_robot/scripts/project_link_env.sh
```

## Existing Fallback Entrypoints

Until systemd field validation is complete, the current scripts remain available:

```bash
./navigation_two_start_mapping.sh
./navigation_two_start_navigation.sh
./navigation_two_status.sh
./navigation_two_save_map.sh
./navigation_two_stop.sh
./scripts/start_visual_grasp_tmux.sh --restart --with-tof
./scripts/start_voice_nav2_stack.sh
bash ./scripts/start_qwen_realtime_voice.sh pure-test
./navigation_two_start_uwb.sh --shadow
```

These scripts are fallback entrypoints, not the final console lifecycle API.

## Safety

- Starting a stack must not send a goal or nonzero velocity.
- Only one `/cmd_vel` control path may run.
- Stop keyboard/direct teleop before Nav2 activation.
- Keep a person at the physical E-stop or power cut during every supervised motion test.
- Do not run Point-LIO and rf2o odometry stacks together.
- Do not run classic and Qwen voice nodes together.
- The LLM never publishes velocity, enables torque or directly calls robot Actions.
- UWB live motion and ToF grasp control remain blocked until their calibration gates pass.

## Git Workflow

`main` is the only active development branch. Commit coherent, verified work
directly to `main`; leave uncertain experiments uncommitted until they prove
useful. Preserve retired experiments with annotated `archive/*` tags, then remove
their active branches and worktrees.

Repository updates flow through GitHub:

```bash
# Windows development repository
git push origin main

# Orin
cd /home/wte/wheeltec_robot
git pull --ff-only
```
