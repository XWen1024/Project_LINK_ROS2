# Project LINK ROS2 Agent Notes

This file is the repo-level operating context for Codex and future coding agents.
Keep it short, current, and actionable. When the project state changes, update this
file together with `HANDOFF.md` and `README.md`.

## Current Priority

- Current phase: SLAM-first validation.
- Do not run Nav2 as the next milestone. Nav2 comes after SLAM/odom/TF are stable
  and after the next SLAM approach is selected.
- Immediate order of work:
  1. Validate lidar data and `/scan`.
  2. Validate the TF tree.
  3. Run the current `rf2o + EKF + slam_toolbox` flow.
  4. Evaluate or switch to a stronger SLAM/odometry approach, likely Point-LIO.
  5. Return to Nav2 only after SLAM and odom are reliable.

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

## SLAM-First Launch Context

Current SLAM candidate:

```bash
ros2 launch turn_on_wheeltec_robot rf2o_slam_toolbox.launch.py
```

Intended data flow:

```text
/scan
-> rf2o_laser_odometry
-> /odom_rf2o
-> robot_localization EKF
-> /odometry/filtered and odom -> base_footprint TF
-> slam_toolbox
-> /map and map -> odom TF
```

Do not blindly launch overlapping TF publishers. In particular, avoid running
multiple EKF/odom publishers that all claim `odom -> base_footprint`.

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

## Documentation Rule

When changing launch flows, hardware assumptions, network settings, or milestone
priority, update:

- `AGENTS.md`
- `HANDOFF.md`
- `README.md`

