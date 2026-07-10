# Project LINK ROS2 Agent Notes

This file is the repo-level operating context for Codex and future coding agents.
Keep it short, current, and actionable. When the project state changes, update this
file together with `PROGRESS.md` and `README.md`.

## Current Priority

- Current phase: SLAM-first validation has a known-good rf2o fallback; the next
  user-requested milestone is a direct RViz A-to-B motion loop with no Nav2,
  planning, costmaps, or obstacle avoidance.
- The minimum loop is: keep SLAM/TF running, click A and B in RViz, then publish
  simple `/cmd_vel` commands to drive directly toward B.
- Immediate order of work:
  1. Keep the working `rf2o + EKF + slam_toolbox` route as the pose source.
  2. Use `scripts/rviz_ab_drive.py --enable-motion` only when the robot is safe.
  3. In RViz, publish two `/clicked_point` points: A as a start sanity check and
     B as the target.
  4. Verify `/cmd_vel`, TF, and odom behavior at low speed.
  5. Continue Point-LIO evaluation separately; do not mix it into this direct
     A-to-B loop unless an explicit adapter/fusion design is implemented.

## Project Summary

Project LINK / Lingxi is a ROS 2 eldercare mobile manipulation robot prototype.
The target MVP is:

```text
voice command
-> structured task command
-> navigation to a manipulation pose
-> object detection
-> SO-101 grasping
-> tray placement
-> return to user
-> TTS feedback
```

This repository currently owns the ROS 2 workspace for the mobile base, SLAM,
Nav2 configuration, message packages, and integration launch/config files.

## Hardware Context

- Main onboard computer: Jetson Orin Nano.
- Orin workspace path: `/home/wte/wheeltec_robot`.
- Previous Orin workspace backup: `/home/wte/wheeltec_robot_backup_20260627_1250`.
- Base controller: STM32-based Wheeltec chassis controller.
- Chassis: differential AGV base, current configured `car_mode: mini_diff`.
- C63A ROS serial link: `/dev/wheeltec_controller` at `115200`; observed as
  `1a86:55d4` on `/dev/ttyACM0`.
- Lidar: Unitree L1 / UniLidar for the target SLAM route; current Wheeltec config
  also contains `lidar_type: ls_M10P_uart`.
- Manipulator: SO-101, currently outside this ROS workspace's main SLAM task.

## ROS 2 Packages In This Workspace

- `turn_on_wheeltec_robot`: base serial node, robot description launch, lidar
  launch, EKF launch, and current SLAM launch entrypoints.
- `wheeltec_nav2`: migrated Nav2 launch/config package. It builds, but Nav2 is not
  the current validation target.
- `wheeltec_slam_toolbox`: slam_toolbox configs and launch wrappers.
- `rf2o_laser_odometry`: laser-scan odometry package.
- `serial`: vendored serial library in `src/depend/serial_ros2`.
- `wheeltec_robot_msg`: custom Wheeltec messages.

## Known Good State

- Date: 2026-06-27.
- Git remote: `git@github.com:XWen1024/Project_LINK_ROS2.git`.
- Main branch migrated from the old Orin workspace and cloned back onto Orin.
- `colcon build --symlink-install` completed successfully for 6 packages.
- `ros2 pkg list` sees the migrated packages.
- `ros2 pkg executables` sees:
  - `turn_on_wheeltec_robot ImuProcessor`
  - `turn_on_wheeltec_robot wheeltec_robot_node`
  - `rf2o_laser_odometry rf2o_laser_odometry_node`
- `patrol_nav2.launch.py` was fixed to use package name `wheeltec_nav2`, not
  directory name `wheeltec_robot_nav2`.
- The previous `rf2o + EKF + slam_toolbox` route was confirmed in RViz2 with a
  valid 2D map on 2026-06-27.
- Point-LIO is built in the external Orin workspace `/home/wte/point_lio_ws`, and
  the repo wrapper `point_lio_unilidar_l1.launch.py --show-args` expands.
- C63A base serial return data was confirmed on 2026-07-11 after power cycling:
  `/odom`, `/imu/data_raw`, and `/PowerVoltage` publish at about 20 Hz.
- The C63A base is integrated into the known-good rf2o SLAM bringup:
  `start_slam_tmux.sh --restart` now starts `base_serial.launch.py`, waits for
  `/odom` and `/scan`, and then starts `rf2o + EKF + slam_toolbox`.
- Differential keyboard teleop helper exists at
  `scripts/ssh_c63_keyboard_teleop.ps1` and `scripts/c63_keyboard_teleop.sh`.
- C63A base and SLAM handoff is documented in
  `docs/C63A_BASE_AND_SLAM_HANDOFF.md`.

## SLAM-First Launch Context

Current SLAM candidate:

```bash
ros2 launch turn_on_wheeltec_robot rf2o_slam_toolbox.launch.py
```

Preferred Orin one-command bringup:

```bash
cd /home/wte/wheeltec_robot
./start_slam_tmux.sh --restart
```

This creates tmux session `project_link_slam` with separate windows for the
Unitree lidar driver, C63A base serial node, pointcloud-to-laserscan plus robot
description, SLAM, and a live topic/TF monitor. It waits for real `/odom` and
`/scan` messages before starting SLAM. It does not start Nav2 and does not
publish `/cmd_vel`. Use `--no-base` only for lidar-only debugging.

Current Point-LIO route:

```bash
cd /home/wte/wheeltec_robot
./start_point_lio_tmux.sh --restart
./start_point_lio_tmux.sh --restart --with-2d-map
```

`./start_point_lio_tmux.sh --restart` is Phase A only and is not expected to
publish `/scan` or `/map`. Use `--with-2d-map` when the user wants a 2D map.
The script waits for real `/unilidar/cloud` and `/unilidar/imu` messages before
launching Point-LIO.

Point-LIO source is intentionally kept outside this repo at
`/home/wte/point_lio_ws/src/point_lio`. The repo-owned integration files are:

- `configs/point_lio/unilidar_l1_project_link.yaml`
- `src/turn_on_wheeltec_robot/launch/point_lio_unilidar_l1.launch.py`
- `start_point_lio_tmux.sh`

Do not run `rf2o_slam_toolbox.launch.py` together with Point-LIO. Only one stack
may publish `odom -> base_footprint`.

Known-good fallback data flow:

```text
/odom from C63A base + /scan from Unitree lidar
-> rf2o_laser_odometry
-> /odom_rf2o
-> robot_localization EKF
-> /odometry/filtered and odom -> base_footprint TF
-> slam_toolbox
-> /map and map -> odom TF
```

Expanded:

```text
/scan
-> rf2o_laser_odometry
-> /odom_rf2o
/odom
-> robot_localization EKF
-> /odometry/filtered and odom -> base_footprint TF
-> slam_toolbox
-> /map and map -> odom TF
```

Point-LIO Phase A data flow:

```text
/unilidar/cloud + /unilidar/imu
-> point_lio
-> /odom_lio and odom -> base_footprint TF
```

Phase A also publishes the static TF `unilidar_link -> unilidar_lidar` from
`point_lio_unilidar_l1.launch.py`, because `unilidar_p2s.launch.py` is not running.

Point-LIO Phase B adds:

```text
/scan + Point-LIO odom TF
-> slam_toolbox
-> /map and map -> odom TF
```

In Phase B, `start_point_lio_tmux.sh --with-2d-map` sets
`publish_lidar_static_tf:=false` for the Point-LIO launch because
`unilidar_p2s.launch.py` already publishes the same static TF.

Point-cloud direction is tuned through `LIDAR_TF_ROLL`, `LIDAR_TF_PITCH`, and
`LIDAR_TF_YAW` on the tmux script. Defaults are roll `3.14159`, pitch `0.0`, yaw
`0.44041`. Keep these values synchronized between Point-LIO Phase A and Phase B.

Do not blindly launch overlapping TF publishers. In particular, avoid running
multiple EKF/odom publishers that all claim `odom -> base_footprint`.
Do not treat Point-LIO's raw 6D pose as the planar `base_footprint` until a
projection/adapter or fusion design is explicitly implemented and validated.

## Direct RViz A-To-B Loop Notes

This project currently has a tiny direct-drive test script:

```bash
source /home/wte/wheeltec_robot/scripts/project_link_env.sh
python3 /home/wte/wheeltec_robot/scripts/rviz_ab_drive.py --enable-motion
```

It subscribes to `/clicked_point`, treats the first RViz point as A/start sanity
check, treats the second point as B/target, looks up `map -> base_footprint`, and
publishes `/cmd_vel` directly. It does no path planning and no obstacle
avoidance.

Use this only with the current SLAM stack already running:

```bash
cd /home/wte/wheeltec_robot
./start_slam_tmux.sh --restart
```

Before enabling motion, verify:

- `/map`, `/scan`, `/odom`, `/odometry/filtered`, and `/cmd_vel` topics exist.
- TF is unique and continuous: `map -> odom -> base_footprint -> base_link`.
- RViz Fixed Frame is `map`.
- RViz has the `Publish Point` tool publishing to `/clicked_point`.
- The robot is physically safe for motion.

## Network Visualization Defaults

Use the same ROS 2 network environment on Orin and the Ubuntu RViz2 computer:

```bash
export ROS_DOMAIN_ID=42
export ROS_LOCALHOST_ONLY=0
```

The repository script `scripts/project_link_env.sh` is the preferred Orin setup
entrypoint. Do not write these settings into `~/.bashrc` unless the user explicitly
asks for persistent shell configuration.

## Safety Rules

- Do not start Nav2 during the current SLAM-first phase unless the user explicitly
  changes the plan.
- Do not publish `/cmd_vel` during hardware bringup unless the robot is safe:
  wheels lifted, motor power disconnected, or a person is ready at the E-stop.
- Prefer read-only checks first: `--show-args`, `ros2 topic list`, `ros2 topic hz`,
  `tf2_echo`, and RViz2 visualization.
- Generated folders and logs stay out of Git: `build/`, `install/`, `log/`, bags,
  temporary maps, and hardware captures.

## Update And Git Rules

- Treat GitHub as the source of truth for repository files.
- Do not update Orin repository files by directly copying over them with `scp`,
  ad-hoc shell writes, or other out-of-band replacement methods.
- Preferred update path:
  1. Edit and test in the local Windows repository.
  2. Commit the coherent change locally.
  3. Push to GitHub.
  4. SSH to Orin and update with `git pull --ff-only`.
- Use smaller, more frequent commits when a change creates a useful rollback
  point, such as launch changes, hardware bringup scripts, configs, or docs that
  change operating procedure.
- Do not commit generated folders, bags, temporary maps, or hardware capture
  outputs unless the user explicitly asks for a curated sample.

## Encoding And Shell Rules

- Markdown and project notes are UTF-8. The repo enforces this with
  `.gitattributes` and `.editorconfig`.
- When using Windows PowerShell to read Chinese text, explicitly use UTF-8:
  `Get-Content -Encoding UTF8 <file>`. If terminal output is still garbled, run
  `chcp 65001` first.
- Prefer `rg` for searching text. When a shell displays mojibake, verify the file
  bytes/content with UTF-8 before assuming the document is corrupted.

## Documentation Rule

When changing launch flows, hardware assumptions, network settings, or milestone
priority, update:

- `AGENTS.md`
- `PROGRESS.md`
- `README.md`
