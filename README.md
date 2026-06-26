# Project LINK ROS2

Project LINK / Lingxi is a ROS 2 based eldercare mobile manipulation robot prototype.

The project focuses on one reproducible MVP first:

```text
voice command
-> structured task command
-> Nav2 navigation to a manipulation pose
-> object detection
-> SO-101 grasping
-> tray placement
-> return to user
-> TTS feedback
```

## Repository Layout

```text
docs/                 Project notes and handoff documents
robot_description/    URDF, meshes, and robot model launch files
bringup/              Top-level launch files for base, sensors, SLAM, and Nav2
configs/              Runtime configuration files
src/                  ROS 2 packages owned by this project
scripts/              Utility scripts
maps/                 Curated maps only, not generated logs
```

## External Dependencies

Unitree L1 / UniLidar SDK is kept outside this repository for now.

Recommended Orin location:

```text
/home/wte/unilidar_sdk
```

This repository should store integration launch/config files, but not vendor build outputs.

## Git Hygiene

Generated ROS 2 workspace folders are intentionally ignored:

```text
build/
install/
log/
```

Keep hardware logs, rosbag recordings, and temporary build artifacts out of Git unless a small sample is intentionally added for documentation or tests.

