# Ubuntu Control Console Architecture

Status: accepted implementation architecture
Last reviewed: 2026-08-15
Target: Ubuntu 22.04 + ROS 2 Humble

## Boundary

- Ubuntu performs all GUI, chart, video, map, costmap and optional RViz2 rendering.
- Orin remains headless and exclusively owns robot hardware and control services.
- The console is a practical toolbox using restrained native Qt controls, not a
  decorative or science-fiction dashboard.

## Components

- `project_link_console_interfaces`: typed state, lifecycle, event and teleop interfaces.
- `project_link_console_agent`: Orin systemd control, health aggregation, log
  forwarding, configuration validation and teleop watchdog.
- `project_link_console_gui`: Ubuntu PySide6 application.
- `deploy/systemd/user`: versioned user services and mapping/navigation targets.

The GUI subscribes directly to ROS maps, costmaps, scans, paths, images and
module status. Process lifecycle is requested through the console agent, which
controls an allowlisted set of `systemd --user` units. Secrets are edited through
an SSH-invoked allowlisted configuration helper and are never transported as ROS
messages.

## Pages

1. Navigation and mapping: system mode, 2D layers, goals, map saving, health and
   supervised dead-man teleoperation.
2. Manipulation: remote video, target tracking, SO-101, presets, calibration,
   ToF and visual grasp.
3. Voice: classic/Qwen exclusive selection, session state, simplified events and
   per-stage timing.
4. Voice configuration: common parameters, prompt profiles and registered tools.
5. UWB: shadow observations, relative plot, distance/residual history and
   proposed calibration capture.
6. Global settings: devices, paths, ROS networking and masked API credentials.

Simple mode exposes only common operator controls. Advanced mode exposes the
full categorized parameter catalog with Chinese labels, units, limits, defaults,
restart requirements and safety notes.

## Visualization And Teleoperation

The built-in view is a Qt 2D renderer for occupancy grids, global/local costmaps,
LaserScan, downsampled XY point-cloud projection, paths, footprint and targets.
Complex 3D point clouds and TF debugging open a repository-owned RViz2 profile
in a separate process.

The Ubuntu GUI never publishes `/cmd_vel` directly. It sends bounded teleop
requests to the Orin agent. The agent publishes only in mapping mode and stops
within 250 ms when the dead-man key, GUI focus, heartbeat, ROS connection or
mode gate is lost. Starting Nav2 disables teleoperation before activation.
