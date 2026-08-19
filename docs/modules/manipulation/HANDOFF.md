# Visual Grasp And SO-101 Handoff

Status: current, pending Orin-to-Ubuntu field validation
Last reviewed: 2026-08-20
Canonical branch: `main`
Key commits: `89e4e1d`, `ef05a47`, `184fee1`, `8e420b4`, `b44d4ce`,
`67dfd26`, `b8cca7e`, `a8cb8a0`, `f712eb3`

## Scope And Boundary

The module provides isolated CUDA YOLO World tracking, SO-101 visual-servo grasping and
VL53L0X near-field distance control. Orin exclusively owns the camera, SO-101
serial bus, model inference and ToF serial port. Ubuntu is a ROS 2 remote client
and must not install or directly use LeRobot hardware drivers.

YOLO-World does not share a Python process with LeRobot. The main node owns
camera capture, SO-101 and control using the validated user-site LeRobot stack.
`project-link-visual-grasp-detector.service` alone uses the isolated `fall-cuda`
site-packages, subscribes to the compressed arm-camera topic and publishes typed
detections. Its Torch 2.8/CUDA 12.6 environment and pinned Ultralytics CLIP are
not visible to the LeRobot process.

The manipulator camera is the Generic/Realtek USB camera with production alias
`/dev/project_link_arm_camera`. It is distinct from the icSpring chassis-front
camera and publishes only on `/visual_grasp/image/compressed`.

It does not own Nav2, SLAM, chassis `/cmd_vel`, cloud VLM recognition or a 3D
depth-grasp planner.

## Runtime Interfaces

- Orin package: `project_link_visual_grasp`
- Ubuntu client: `project_link_visual_grasp_gui`
- ToF package: `project_link_vl53l0x`
- Images/status: `/visual_grasp/image/compressed`, `/visual_grasp/camera_status`,
  `/visual_grasp/status`
- CUDA detector: `/visual_grasp/detector/target`,
  `/visual_grasp/detector/config`, `/visual_grasp/detector/result`
- Discovery: `/project_link_visual_grasp/discovery`
- ToF: `/visual_grasp/tof_range`, `/visual_grasp/tof_status`
- Action: `/visual_grasp/track_and_grasp`
- Services cover target, gripper, connection, torque, stop, presets,
  calibration and demonstration recording.

## Verified State

- The current hardware-independent regression contains 60 passing visual-grasp
  scenarios; VL53L0X protocol cases also pass.
- Windows hardware testing covered camera, YOLO World, SO-101 calibration,
  preset motion, ToF serial input, logging and supervised automatic grasp.
- One test closed the gripper at approximately `0.088 m` ToF range.
- The unsafe fixed elbow polynomial and shoulder-lift image-Y correction were
  removed. Current approach uses bounded taught shoulder/elbow/wrist motion.
- ESP32-C3 firmware emitted a valid `43 mm`, status-0 frame.
- On 2026-08-15 the embedded panel built and initialized on the Ubuntu 22.04
  Humble laptop. The visible read-only console remains active and the panel shows
  the expected disconnected state while the Orin visual-grasp service is stopped.
- On 2026-08-20 the Generic arm camera was verified as native MJPEG
  `1280x720@30 FPS`; YUYV at the same resolution is limited to 10 FPS. The main
  node now publishes native JPEG without re-encoding and decodes only when an
  in-process fallback tracker actually needs a frame.
- The isolated CUDA detector loaded Torch `2.8.0`, CUDA `12.6`, Ultralytics
  `8.4.32` and the YOLO-World model on `cuda:0`. A real `1280x720` camera frame
  completed inference in approximately `56.8 ms`; no arm service or joint command
  was invoked during validation.
- Ubuntu passed 67 related tests and directly instantiated the restored visual
  servo panel offscreen. The page supports click-selected alignment points,
  reset/current-box shortcuts, five box anchors and a separate visual-servo start.

## Console Integration

The former standalone `VisualGraspWindow` now wraps a reusable
`VisualGraspPanel(QWidget)`. `project_link_console_gui` embeds that panel as its
mechanical-arm page while retaining the standalone Ubuntu entrypoint. Simple mode
hides the raw Orin parameter editor; advanced mode reveals it. Constructing the
page creates only the remote ROS client and does not start the Orin visual-grasp,
SO-101 or VL53L0X services.

The Ubuntu Humble `rclpy` package does not provide `rclpy.parameter_client`.
Remote parameter editing therefore uses explicit `GetParameters` and
`SetParameters` service clients. Page-construction exceptions are retained after
Qt signal connections are installed, printed to the journal and summarized in
the console log. Qt owns SIGINT/SIGTERM while the console is running so rapid
systemd restarts cannot shut down the ROS context halfway through page creation.

The video overlay uses a yellow cross for the operator-selected image target and
a green dot for the selected detection-box anchor. Pressing “开始视觉伺服” keeps
the controller in a tracking servo state after alignment instead of continuing
into the grasp approach. Horizontal correction is the default. Vertical
shoulder-lift correction has an explicit supervised checkbox and remains off
until its direction and limits are field-validated.

## Remaining Gates

- Build and validate the new ROS ToF package on Orin.
- With the arm physically supported and torque off, install/reload the new user
  units, restart the main/detector pair and verify native MJPEG + CUDA status in
  the visible Ubuntu GUI.
- Complete the supervised horizontal visual-servo loop, then separately validate
  vertical correction before leaving that option enabled.
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
