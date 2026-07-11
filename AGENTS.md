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
  5. Complete the Point-LIO Phase A planar projection validation before using
     Point-LIO for a 2D map or any A-to-B motion test.

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
- `project_link_voice_interfaces`: `DriveToPoint.action` for voice direct-drive.
- `project_link_emergency_interfaces`: fall-response Action/Service interfaces.
- `project_link_voice`: FunASR VAD voice dialog, local TTS bridge, and guarded
  direct-drive nodes.
- `project_link_fall_response`: second-camera fall assessment, SiliconFlow
  vision call, TTS alert, and Feishu bot notification bridge.
- `project_link_visual_grasp`: headless Orin YOLO-World camera, SO-101 control,
  and visual-servo ROS services/actions.

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
- The Point-LIO planar projection package was built and brought up on Orin on
  2026-07-11. Phase A publishes real `/unilidar/cloud` at about 9.8 Hz,
  `/unilidar/imu` at about 249 Hz, `/odom_lio_raw`, `/odom_lio`, and
  `/point_lio/cloud_registered` at lidar cadence. The raw output has a single
  Point-LIO publisher and the planar output has a single adapter publisher.
- The projected `odom -> base_footprint` TF has verified `z=0`, roll `0`, and
  pitch `0`. Its observed yaw is about `-65 degrees` before the required
  straight-line calibration; do not treat that yaw as a final chassis heading.
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
publish `/scan` or `/map`. It waits for real `/unilidar/cloud` and
`/unilidar/imu` messages before launching Point-LIO. Do not use
`--with-2d-map` until Phase A TF validation has passed.

Point-LIO source is intentionally kept outside this repo at
`/home/wte/point_lio_ws/src/point_lio`. The repo-owned integration files are:

- `configs/point_lio/unilidar_l1_project_link.yaml`
- `configs/point_lio/lio_planar_projection.yaml`
- `src/turn_on_wheeltec_robot/launch/point_lio_unilidar_l1.launch.py`
- `src/turn_on_wheeltec_robot/src/lio_planar_projection.cpp`
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
-> /odom_lio_raw and lio_odom -> lio_base TF (raw 3D pose)
-> lio_planar_projection
-> /odom_lio and odom -> base_footprint TF (planar pose)
```

The raw Point-LIO pose is intentionally not a planar chassis frame. The adapter
publishes `odom -> lio_odom` as a static alignment, then dynamically publishes
`lio_base -> base_footprint` so the composed base TF has `z=0`, `roll=0`, and
`pitch=0`. Tune only the versioned projection YAML after the stationary and
straight-line hardware checks.

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
multiple EKF/odom publishers that all claim `odom -> base_footprint`. Point-LIO
owns only `lio_odom -> lio_base`; `lio_planar_projection` owns the planar base
TF in the Point-LIO stack.

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

## Guarded Voice Direct-Drive Integration

- Workspace-owned production packages: `project_link_voice_interfaces` defines
  `DriveToPoint.action`; `project_link_voice` contains FunASR VAD, voice dialog,
  TTS bridge, and guarded direct-drive nodes.
- This is an experimental extension of the direct RViz A-to-B milestone, not a
  Nav2 replacement. It has no obstacle avoidance, costmaps, or path planning.
- `ros2 launch project_link_voice voice_direct_drive.launch.py` defaults to
  `enable_motion:=false`. Do not set it true until `/map`, `/scan`, `/odom`, and
  `map -> base_footprint` are healthy and a physical E-stop is available.
- Do not run `ab_drive_server` concurrently with
  `scripts/rviz_ab_drive.py --enable-motion`; both publish `/cmd_vel`.
- FunASR `fsmn-vad` replaces RMS recording cutoff. Keep its model and the
  faster-whisper model pre-downloaded on Orin; use an Orin-specific virtual
  environment with a JetPack-compatible PyTorch build.
- Voice motion is local and guarded: only named map waypoints, explicit
  `确认前往`, then the `DriveToPoint` Action. `停止`/`取消` cancels and publishes
  zero velocity, but it does not replace a physical E-stop.

## Voice-To-Grasp Task Integration

- `voice_dialog_node` can parse guarded fetch commands such as `去厨房拿药瓶`.
  It maps the named waypoint to direct drive and maps spoken object aliases to
  YOLO-World targets, for example `药瓶=medicine bottle`.
- The fetch chain is only valid after the direct-drive Action succeeds and the
  base has stopped at a verified safe manipulation pose. Then it may call
  `/visual_grasp/connect_arm`, `/visual_grasp/set_torque`, and
  `/visual_grasp/track_and_grasp`.
- `enable_visual_grasp:=false` is the default. Do not set it true unless the
  visual grasp stack is running, SO-101 space is clear, and physical E-stop or
  power-cut procedure is ready.

## Voice-Triggered Fall Response

- `project_link_fall_response` is a separate emergency module. It uses the
  second camera device, default `/dev/FallCam`, and must not share the visual
  grasp camera `/dev/RgbCam`.
- The audio project owns wake-word detection, sound-source localization, and any
  controlled turn toward the person. The fall module only starts after audio
  calls `/fall_detection/assess_fall`.
- If SiliconFlow returns strict JSON with `fall_suspected=true` above threshold,
  the module publishes `您看起来摔倒了，正在为您呼叫紧急联系人。` on
  `/voice/tts_text`, waits 15 seconds for `/fall_detection/confirm_alert`, and
  pushes a Feishu bot alert on confirmation or timeout.
- Missing `SILICONFLOW_API_KEY` or Feishu bot environment values must fail
  closed. Do not commit API keys, webhook URLs, signing secrets, captured
  images, or cloud response logs.
- The module must not publish `/cmd_vel`, start Nav2, control SO-101, or replace
  a physical E-stop/emergency procedure. See
  `docs/VOICE_FALL_DETECTION_INTEGRATION.md` before changing the audio contract.

## Headless YOLO World Visual Grasp

- `project_link_visual_grasp` owns the Orin V4L2 camera, local YOLO-World model,
  and SO-101 serial connection. `project_link_visual_grasp_gui` is Ubuntu-only;
  it must never open the camera, load Ultralytics/LeRobot, or command serial
  hardware directly.
- Do not import or migrate `VisualTracker/main.py`, `src/vlm_detector.py`, or
  the cloud/VLM pipeline. This ROS integration is local YOLO-World only.
- Start `scripts/start_visual_grasp_tmux.sh` separately after any required SLAM
  bringup. It must not start Nav2, publish `/cmd_vel`, or become another TF/odom
  publisher.
- Runtime GUI tuning and recorded poses live under
  `~/.config/project_link/visual_grasp/` on Orin. Do not write runtime changes
  into Git-tracked YAML files on the robot.
- Before torque, manual approach, or `TrackAndGrasp` action tests, verify a clear
  workspace and physical E-stop/power-cut procedure. The scheduler action is
  only valid after navigation reaches a safe manipulation pose.
