# Visual Grasp And SO-101 Handoff

Status: current, pending Orin-to-Ubuntu field validation
Last reviewed: 2026-08-15
Canonical branch: `main`
Key commits: `89e4e1d`, `ef05a47`

## Scope And Boundary

The module provides local YOLO World tracking, SO-101 visual-servo grasping and
VL53L0X near-field distance control. Orin exclusively owns the camera, SO-101
serial bus, model inference and ToF serial port. Ubuntu is a ROS 2 remote client
and must not install or directly use LeRobot hardware drivers.

It does not own Nav2, SLAM, chassis `/cmd_vel`, cloud VLM recognition or a 3D
depth-grasp planner.

## Runtime Interfaces

- Orin package: `project_link_visual_grasp`
- Ubuntu client: `project_link_visual_grasp_gui`
- ToF package: `project_link_vl53l0x`
- Images/status: `/visual_grasp/image/compressed`, `/visual_grasp/status`
- Discovery: `/project_link_visual_grasp/discovery`
- ToF: `/visual_grasp/tof_range`, `/visual_grasp/tof_status`
- Action: `/visual_grasp/track_and_grasp`
- Services cover target, gripper, connection, torque, stop, presets,
  calibration and demonstration recording.

## Verified State

- The current hardware-independent regression contains 52 passing visual-grasp
  scenarios; VL53L0X protocol cases also pass.
- Windows hardware testing covered camera, YOLO World, SO-101 calibration,
  preset motion, ToF serial input, logging and supervised automatic grasp.
- One test closed the gripper at approximately `0.088 m` ToF range.
- The unsafe fixed elbow polynomial and shoulder-lift image-Y correction were
  removed. Current approach uses bounded taught shoulder/elbow/wrist motion.
- ESP32-C3 firmware emitted a valid `43 mm`, status-0 frame.

## Console Integration

The former standalone `VisualGraspWindow` now wraps a reusable
`VisualGraspPanel(QWidget)`. `project_link_console_gui` embeds that panel as its
mechanical-arm page while retaining the standalone Ubuntu entrypoint. Simple mode
hides the raw Orin parameter editor; advanced mode reveals it. Constructing the
page creates only the remote ROS client and does not start the Orin visual-grasp,
SO-101 or VL53L0X services.

## Remaining Gates

- Build and validate the new ROS ToF package on Orin.
- Complete the full Orin camera/model/SO-101/ToF to Ubuntu GUI loop.
- Verify discovery, compressed video, remote parameter persistence and restart recovery.
- Resolve approach-stage horizontal drift and final grasp height with additional
  supervised demonstrations; do not enlarge limits or blind travel to hide it.

## Safety Invariants

- ToF control requires enabled, control-enabled, calibrated, valid and fresh data.
- Far-range bbox size alone cannot authorize blind final approach.
- Normal stop holds the current joints; emergency stop disables torque directly.
- Overheat or unconfirmed torque-off latches a fault until physical cooling and reconnect.
- Real movement requires a clear workspace and physical power-cut procedure.

Detailed interfaces, deployment and calibration are in `INTERFACES.md`,
`ORIN_SETUP.md`, `CALIBRATION.md` and `WINDOWS_LAB.md`.
