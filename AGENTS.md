# Project LINK ROS2 Agent Notes

This file is the repo-level operating context for Codex and future coding agents.
Keep it short, current, and actionable. When the project state changes, update this
file together with `PROGRESS.md` and `README.md`.

## Current Priority

- Current phase: Point-LIO Phase B, live slam_toolbox mapping, C63A velocity
  feedback, and Nav2 are running as a known-good supervised navigation stack.
  The immediate software milestone is BU03/BU04 UWB summon and person-following
  through Nav2, first in shadow mode and then through measured calibration and
  supervised low-speed field gates. Navigation Two repeatability and endurance
  remain prerequisites for UWB live motion.
- The durable Navigation2 handoff is `docs/NAVIGATION_TWO_HANDOFF.md`. Keep it
  synchronized with this file and `PROGRESS.md` when navigation behavior changes.
- The minimum loop is: save a good map, save named voice waypoints, dry-run
  ASR/LLM/TTS, enable direct point-to-point drive, validate visual grasp alone,
  then allow voice fetch.
- Immediate order of work:
  1. Keep the working `rf2o + EKF + slam_toolbox` route as the fallback.
  2. Apply `scripts/apply_point_lio_realtime_patch.sh` to the external Point-LIO
     checkout, rebuild it, and start Phase B with `start_point_lio_tmux.sh`.
  3. Confirm LIO timestamp lag remains bounded before starting Nav2.
  4. Use `start_point_lio_nav2_tmux.sh --restart` to start Nav2 only.
  5. Validate both costmaps and path planning before sending a motion goal.
  6. Stop keyboard teleop before any Nav2 goal; only one `/cmd_vel` publisher may
     control the base during the supervised low-speed navigation test.
  7. Tune the 2D height slice only if navigation costmaps are materially harmed.
  8. Save a good map and named voice waypoints after the navigation check.
  9. Add voice motion and visual grasp only after each subsystem is safe.
  10. Build and test `project_link_uwb_interfaces` and
      `project_link_uwb_navigation` offline, then validate BU04 serial and map
      targets in shadow mode.
  11. Do not enable UWB live motion until the BU04-to-`base_footprint`
      calibration is operator-approved and stop/takeover fault tests pass.

Navigation Two convenience entrypoints are repository-root scripts:

- `navigation_two_start.sh` / `navigation_two_start_navigation.sh`: base + live
  Point-LIO map + Nav2, with no goal sent.
- `navigation_two_start_mapping.sh`: base + live map, with Nav2 stopped for
  keyboard teleop.
- `navigation_two_save_map.sh`: occupancy map plus best-effort posegraph save.
- `navigation_two_status.sh`: consolidated read-only tmux monitor.
- `navigation_two_stop.sh`: publish zero velocity, then stop the full stack.

All Navigation Two topic gates must retry discovery until their deadline. A
single `timeout ros2 topic echo` is insufficient because `ros2 topic echo` exits
immediately when a newly launched publisher's type has not reached the graph yet.

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
- Default SSH target: `wte@orin`. Use this hostname instead of a fixed IP for
  repository runbooks, helper scripts, and interactive hardware work.
- Orin workspace path: `/home/wte/wheeltec_robot`.
- Previous Orin workspace backup: `/home/wte/wheeltec_robot_backup_20260627_1250`.
- Base controller: STM32-based Wheeltec chassis controller.
- Chassis: differential AGV base, current configured `car_mode: mini_diff`.
- Conservative measured outer envelope on 2026-08-03: `0.51 m` long,
  `0.41 m` wide, and `0.82 m` high. The canonical URDF and live Nav2 footprint
  use this length/width; `0.01 m` Nav2 padding gives an effective `0.53 x 0.43 m`
  collision envelope.
- C63A ROS serial link: `/dev/wheeltec_controller` at `115200`; observed as
  `1a86:55d4` on `/dev/ttyACM0`.
- BU04 has two Type-C ports. The port marked `USB` is the STM32F103 native CDC
  interface (`0483:5740`) and must own the ROS measurement alias
  `/dev/uwb-bu04`. The port marked `TTL` is CH340 (`1a86:7523`) and is reserved
  as `/dev/uwb-bu04-at` for AT/configuration work. The exact Jetson
  `5.15.185-tegra` CH341 module remains installed for that optional TTL path.
- BU04 firmware reports `V1.0.0`, PDoA mode, base role, JSON output, 100 ms
  rate, filtering enabled, and one paired tag. The invalid saved `AncID:65535`
  was replaced with `AncID:1`, network `0x1111`, and persisted with `AT+SAVE`.
  A physical disconnect/reconnect proved that the cold boot loads the new
  configuration and reports no firmware error bits. Windows testing then found
  the root cause of the empty stream: CH340 `COM25` was the TTL/AT port, while
  native USB `COM26` immediately emitted valid `JS + length + JSON` PDoA frames.
  A Windows 10-second capture contained 289/289 valid frames (`28.9 Hz`). Orin
  then enumerated the same port as `/dev/ttyACM1` with `0483:5740` on the expected
  physical USB path and parsed 288/288 valid frames in 10 seconds (`28.8 Hz`).
  Do not reflash; deploy the corrected udev rule through GitHub, then validate ROS
  ingestion in shadow mode before any navigation action.
- Lidar: Unitree L1 / UniLidar for the target SLAM route; current Wheeltec config
  also contains `lidar_type: ls_M10P_uart`.
- Manipulator: SO-101, currently outside this ROS workspace's main SLAM task.
- ESP32-C3 + VL53L0X bench bridge: native USB Serial/JTAG 303a:1001, I2C on
  GPIO4/GPIO5, 50 ms text output, and a valid 43 mm/status-0 measurement were
  verified on 2026-08-10. Firmware and the bench GUI live under
  external/esp32_c3_vl53l0x_usb_bridge. The repository now also contains the
  `project_link_vl53l0x` headless ROS package described in
  docs/ESP32_C3_VL53L0X_USB_ROS2_HANDOFF.md; Orin build and mounted-sensor field
  validation remain pending. The ROS node publishes sensor_msgs/Range, owns the
  serial port exclusively, and never publishes cmd_vel.

## ROS 2 Packages In This Workspace

- `turn_on_wheeltec_robot`: base serial node, robot description launch, lidar
  launch, EKF launch, and current SLAM launch entrypoints.
- `wheeltec_nav2`: live Point-LIO Nav2 launch/config package and the current
  supervised navigation validation target.
- `wheeltec_slam_toolbox`: slam_toolbox configs and launch wrappers.
- `rf2o_laser_odometry`: laser-scan odometry package.
- `serial`: vendored serial library in `src/depend/serial_ros2`.
- `wheeltec_robot_msg`: custom Wheeltec messages.
- `project_link_voice_interfaces`: `DriveToPoint.action` for voice direct-drive.
- `project_link_emergency_interfaces`: fall-response Action/Service interfaces.
- `project_link_voice`: FunASR VAD, Volcano bidirectional streaming ASR with
  explicit faster-whisper fallback, DeepSeek official LLM tool calling,
  Volcano bidirectional TTS, guarded direct-drive, and voice-to-grasp orchestration.
- `project_link_fall_response`: second-camera fall assessment, SiliconFlow
  vision call, TTS alert, and Feishu bot notification bridge.
- `project_link_visual_grasp`: headless Orin YOLO-World camera, SO-101 control,
  and visual-servo ROS services/actions.
- `project_link_vl53l0x`: sole serial owner for the ESP32-C3 VL53L0X bridge;
  publishes the SO-101 end-effector Range topic and status only.
- `project_link_uwb_interfaces`: UWB observation message and long-running
  summon/follow Action interface.
- `project_link_uwb_navigation`: bounded BU04 serial decoder, calibration and
  target policy, Nav2 rolling-goal server, and optional stdio MCP bridge.

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
- On 2026-08-03 the visually verified sensor correction became roll `pi`, pitch
  `0`, yaw `2.0112063268 rad` (`115.234 degrees`). Unitree's factory LiDAR/IMU
  axes remain parallel, so Point-LIO keeps identity `extrinsic_R` and the vendor
  millimetre-scale `extrinsic_T`.
- The corrected IMU/LIO-body to `base_footprint` transform is versioned in
  `lio_planar_projection.yaml`. It reduced the false planar lever arm from about
  `0.94 m` to about `0.20 m`, matching the physical `0.19 m` forward mounting.
  Stationary output has only centimetre-scale jitter and a supervised low-speed
  90-degree in-place turn matched the physical motion without the previous large
  circular path. The old `odom_to_lio_odom_yaw: 1.135` calibration is invalid
  after this correction and has been reset to `0.0`. The subsequent supervised
  straight-line test also matched the physical chassis motion.
- After the canonical package xacro became the sole sensor TF authority, Phase B
  was rebuilt and verified on Orin: `/scan` is about `9.34 Hz`, `/odom_lio` about
  `9.31 Hz`, `/map` publishes, and `map -> base_footprint` is continuous. The only
  `/tf_static` publishers are `robot_state_publisher` and the intentional
  `odom -> lio_odom` world-alignment publisher.
- Phase B diagnostics found that a raw `/scan` frame has only about `129/723`
  valid angular bins (`17.8%`) split into roughly 57 disconnected segments. RViz
  Decay Time 3 looks complete because it motion-compensates and overlays about
  30 frames, reaching about `46.1%` union coverage. slam_toolbox must therefore
  consume the TF-compensated rolling `/scan_accumulated`, not each raw fragment.
- The accumulator was built and field-started on Orin. With the final `3.0 s`
  window, `0.04 m` voxel, and `25%` startup threshold, it warmed at `25.3%`,
  stabilized around `29%` coverage while stationary, and published at about
  `9.49 Hz`. slam_toolbox registered the accumulated sensor successfully.
- A first user-driven mapping lap produced coherent rooms, corridors, and major
  wall outlines. Remaining edge speckle is acceptable for initial Nav2 costmap
  validation and should be handled conservatively by the footprint/inflation
  layers before further height-slice tuning.
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
`pitch=0`.

The single sensor-mounting TF authority is the installed package xacro:
`src/turn_on_wheeltec_robot/urdf/patrol_robot.urdf.xacro`. It publishes the full
`base_footprint -> base_link -> unilidar_link -> unilidar_lidar -> unilidar_imu`
chain through `robot_state_publisher`. Point-LIO and pointcloud-to-laserscan must
not publish duplicate sensor static transforms.

Point-LIO Phase B adds:

```text
/scan + timestamped Point-LIO odom TF
-> laser_scan_accumulator
-> /scan_accumulated
-> slam_toolbox
-> /map and map -> odom TF
```

Phase B defaults to the Orin real-time profile: `odom_only=true`, input point
filter `2`, surface/map voxels `0.15 m`, local cube `150 m`, and detection range
`40 m`. It therefore does not publish registered/map point clouds or `/path_lio`
during normal 2D mapping/navigation. Phase A keeps `odom_only=false` so the raw
3D outputs remain available for deliberate RViz inspection. All values can be
overridden with the `POINT_LIO_*` environment variables documented by
`start_point_lio_tmux.sh --help`.

The external Point-LIO checkout must also carry the repository-owned patch
`patches/point_lio/0001-bound-realtime-queues.patch`. Apply it only through
`scripts/apply_point_lio_realtime_patch.sh`; the script is idempotent and does
not reset unrelated changes in the dirty external checkout. The patch limits
the internal LiDAR queue to 2 frames and IMU queue to 2000 messages, drops stale
oldest data, reduces ROS QoS/publisher depths, fixes fractional LiDAR time, and
prints throttled queue/backlog diagnostics. Point-LIO source remains outside
this repository at `/home/wte/point_lio_ws/src/point_lio`.

The accumulator keeps raw `/scan` for RViz/debug, transforms every finite scan
endpoint into `odom` at its source timestamp, maintains a short rolling window,
then transforms and re-bins the points into the current `base_link`. Do not
replace it with a base-frame-only decay buffer; that smears walls while moving.

The verified sensor direction and Unitree LiDAR/IMU origin offset are versioned
only in the canonical xacro. Change that file and revalidate Phase A if the
physical mounting changes.

Do not blindly launch overlapping TF publishers. In particular, avoid running
multiple EKF/odom publishers that all claim `odom -> base_footprint`. Point-LIO
owns only `lio_odom -> lio_base`; `lio_planar_projection` owns the planar base
TF in the Point-LIO stack.

## Point-LIO Live Nav2

Use the Nav2-only wrapper while Point-LIO Phase B is already healthy:

```bash
cd /home/wte/wheeltec_robot
./start_point_lio_nav2_tmux.sh --restart
```

It starts no AMCL, map server, slam_toolbox, lidar, robot description, odometry,
or base node. The live `/map` and `map -> odom -> base_footprint` chain remain
owned by Phase B. Nav2 uses Point-LIO TF for pose, C63A `/odom` only for
chassis-frame velocity, and `/scan_accumulated` for obstacles, with the physical
`0.51 x 0.41 m` measured footprint and conservative first-test velocity limits.

Do not switch Nav2 velocity feedback back to `/odom_lio` without first fixing
the projected twist semantics. Point-LIO stores linear velocity in its world
frame while `nav_msgs/Odometry.twist` is expected in the child/body frame; the
current projection preserves pose correctly but does not make that raw twist a
valid differential-drive velocity. C63A `/odom` supplies body-frame twist while
Point-LIO remains the sole navigation pose/TF source.

Because the Nav2-only wrapper does not start the chassis, start
`base_serial.launch.py` separately before it. The wrapper now waits for real
`/odom` in addition to `/map`, `/odom_lio`, and `/scan_accumulated`.

The keyboard teleop publishes `/cmd_vel` continuously, including zero commands,
so the wrapper refuses to start while it is running. Starting Nav2 does not send
a goal or a nonzero command. Before using RViz `2D Goal Pose`, verify lifecycle
nodes and costmaps, clear the robot area, and keep the physical E-stop ready.

The first live-map Nav2 bringup passed on Orin on 2026-08-03. DWB, NavFn, both
costmap layers, behavior plugins, BT Navigator, waypoint follower, and velocity
smoother configured and reached `active`; `/navigate_to_pose` is available. The
global costmap loaded the live map at `158 x 174` cells with `0.05 m` resolution,
and no `/cmd_vel` message was observed without a goal.

During the first costmap visualization, long-running Point-LIO accumulated about
`21 s` of odometry timestamp lag while raw `/scan` remained current. Save the
map before restarting Phase B if this recurs; the restart restored raw/projected
odom lag to about `0.03 s`. The saved occupancy map from this incident is
`/home/wte/maps/point_lio_nav2_pre_restart_20260803_2312.yaml`.

The local obstacle source must explicitly accept `-0.1..2.0 m` height because
`base_link` is above `base_footprint`. Without this range, Humble filtered every
LaserScan point and published an all-free local costmap. The verified local
window is `3 x 3 m` at `0.05 m`; a stationary sample contained 69 lethal and 986
inflated cells.

The initial `0.40 x 0.35 m` model was smaller than the measured outer envelope,
especially in length. The current footprint is centered on `base_footprint` at
`+/-0.255 m` longitudinal and `+/-0.205 m` lateral, with `0.01 m` padding. If
corner clearance remains asymmetric, measure front/rear distance from the real
drive-wheel midpoint rather than enlarging the centered rectangle blindly.

The live-map navigation trees are
`behavior_trees/point_lio_safe_replanning.xml` and its multi-pose companion
`point_lio_safe_through_poses.xml`. The single-goal tree checks path validity at
2 Hz but keeps a still-valid path instead of replacing it every second as the
online map expands. Recovery clears both costmaps, attempts a collision-checked
full `360 degree` scan spin, clears again, and replans; it must never command
reverse because the current chassis has no reliable rear obstacle coverage. DWB also has
`min_vel_x: 0.0`, and the velocity smoother has no negative linear limit.

The first path-following diagnosis found small backtracking segments in raw
NavFn plans, repeated global-map resize events, `No valid trajectories` from
the DWB obstacle critic, and false/late `Failed to make progress` recoveries.
The first corrective pass proved too conservative: full-footprint DWB checking
rejected all 619 trajectories in several open-looking cases, and collision-
checked smoothing repeatedly rejected otherwise usable NavFn paths. The current
midpoint tuning therefore uses `BaseObstacle`, a `0.40 m` inflation radius with
`3.5` cost scaling, no path smoothing, stable validity-triggered replanning, and
a `0.10 m / 20 s` progress check. Do not re-enable `BackUp` unless a verified
rear obstacle sensor is integrated.

RViz navigation goal completion uses `0.25 m` XY and `0.50 rad` yaw tolerance.
The earlier `0.15 m / 0.20 rad` checker kept a visible path active after the
robot was practically at the destination, then allowed the XY-only progress
checker to trigger recovery while DWB chased the final orientation. A recovery
now attempts at most one full scan turn, with a 20-second allowance so a
`6.28 rad` spin at the configured speed can actually finish.

## UWB Summon And Follow

- Durable handoff: `docs/UWB_SUMMON_AND_FOLLOW_HANDOFF.md`.
- `navigation_two_start_uwb.sh` starts UWB ingestion and the guarded Nav2 bridge
  only after Navigation Two is already healthy. It sends no goal itself.
- Default mode is shadow: publish `/uwb_navigation/proposed_goal`, never call
  Nav2. Live mode requires `--enable-motion --confirm-motion UWB-NAV2`, a local
  calibration YAML marked `valid`, the exact stable BU04 device, and a private
  tag address supplied through `PROJECT_LINK_UWB_TAG_ADDRESS`.
- `navigation_two_uwb.sh status|summon|follow|stop` is the operator entrypoint.
- Summon submits exactly one static Nav2 goal per Action request. Only follow
  mode may cancel and replace rolling Nav2 goals; never apply follow's refresh
  timer to summon.
- The UWB launcher creates a bootstrap tmux window, sets the private device/tag
  environment on the session, then creates the real node window so the values
  are inherited without appearing in the launch command. Readiness requires a
  real `/uwb/person_observation`, not a one-shot status message.
- Keep the launch-time private tag wrapped in a string-typed `ParameterValue`;
  ROS launch otherwise infers digits-only addresses as integers and the serial
  node rejects the parameter before opening the BU04 stream.
- Keep `PersonNavigation.action`'s Goal free of dynamic strings on the current
  Humble Orin. Fast DDS rejected the redundant caller-facing `source_id` before
  the goal reached the server; source selection remains local and config-bound.
- The UWB launch and operator scripts default only their own processes to
  `rmw_cyclonedds_cpp`. Do not change the Navigation Two middleware globally;
  Cyclone/Fast DDS interoperability on domain 42 preserves the Nav2 boundary.
- For single-tag shadow calibration after BU04 power cycles, use
  `scripts/start_uwb_shadow_auto_tag.py`. It verifies native STM32 USB identity,
  never prints/persists the private address, and cannot request live motion.
- Shadow startup must remain independent of chassis/SLAM/Nav2 health so raw
  four-direction UWB calibration can run standalone. Only live motion performs
  the full Navigation Two prerequisite gate; map goals still require map TF.
- On the current Orin, build the two UWB packages without `--symlink-install`.
  `setuptools 82` rejects Humble colcon's legacy `setup.py develop --editable`;
  normal install mode is verified and avoids changing the global Python stack.
- UWB never publishes `/cmd_vel`; only Nav2 `velocity_smoother` may own that
  topic. People too close, stale UWB/TF, serial loss, Nav2 failure, cancellation
  failure, or an extra velocity publisher must cancel/abort fail-closed.
- The repository calibration is intentionally invalid. Do not replace it with
  guessed axes or mount offsets. Measure four directions and multiple distances,
  validate map targets in shadow mode, then approve a local runtime file.
- Automatic loss-search is disabled in the first ROS 2 version. Loss cancels the
  Nav2 goal and stops; add bounded Nav2 Spin only after rear-sector PDoA and
  cancellation behavior are evidenced.
- Nav2's expected `/cmd_vel` publishers are `velocity_smoother` and
  `behavior_server`; any publisher outside that set still rejects live UWB goals.
- The first supervised live summon on 2026-08-06 exposed a goal-lifecycle bug:
  the shared `0.75 s` follow refresh canceled the summon goal after about
  `0.83 s`, producing only a brief movement. The server now keeps summon
  one-shot and reserves throttled replacement for follow; repeat live validation
  is still required before accepting the summon gate.
- The second supervised attempt proved the static goal was no longer refreshed,
  but post-submission PDoA jitter tripped the shared `3.0 m/s` moving-target gate
  and canceled motion after about `0.45 s`. After a summon submits its one-shot
  goal, freeze the target and use later observations only for freshness and
  range-arrival safety; moving-target speed validation remains follow-only.

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

- Do not start Nav2 automatically during hardware bringup. The user has enabled
  supervised low-speed Nav2 validation only after Point-LIO Phase B is healthy.
- Do not publish `/cmd_vel` during hardware bringup unless the robot is safe:
  wheels lifted, motor power disconnected, or a person is ready at the E-stop.
- Prefer read-only checks first: `--show-args`, `ros2 topic list`, `ros2 topic hz`,
  `tf2_echo`, and RViz2 visualization.
- Generated folders and logs stay out of Git: `build/`, `install/`, `log/`, bags,
  temporary maps, and hardware captures.

## Update And Git Rules

- Treat GitHub as the source of truth for repository files.
- `main` is the sole long-lived integration and development branch. Routine
  work should be based directly on the latest `main`; do not create feature,
  experiment, or worktree branches unless the user explicitly requests one or
  a special isolation requirement has first been explained and approved.
- Consolidate every change that is still needed from existing branches onto the
  latest `main`, verify the combined result there, then archive experimental,
  superseded, and otherwise unnecessary branches and remove their active
  worktrees. Archived experimental code must remain recoverable through an
  annotated tag or an `archive/*` remote ref, but must not remain an active
  development line.
- Do not commit speculative work merely to save it. For an uncertain experiment,
  keep the relevant changes uncommitted while testing them; if the result is not
  useful, discard only that experiment without disturbing unrelated user work.
  If it is useful and verified, make a coherent commit directly on `main`.
- Do not rewrite published `main` history to make it look cleaner. Restore order
  through forward commits, merges, archival tags, and branch/worktree cleanup.
- Do not update Orin repository files by directly copying over them with `scp`,
  ad-hoc shell writes, or other out-of-band replacement methods.
- Preferred update path:
  1. Edit and test in the local Windows repository.
  2. Commit the coherent change locally.
  3. Push to GitHub.
  4. SSH to `wte@orin` and update with `git pull --ff-only`.
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
  Volcano TTS, LLM tool calling, and guarded direct-drive nodes.
- The fastest site runbook is `docs/SITE_VOICE_MOBILE_MANIPULATION_RUNBOOK.md`.
  Prefer its scripts over hand-assembling long launch commands during field work.
- This is an experimental extension of the direct RViz A-to-B milestone, not a
  Nav2 replacement. It has no obstacle avoidance, costmaps, or path planning.
- `ros2 launch project_link_voice voice_direct_drive.launch.py` defaults to
  `enable_motion:=false`. Do not set it true until `/map`, `/scan`, `/odom`, and
  `map -> base_footprint` are healthy and a physical E-stop is available.
- Do not run `ab_drive_server` concurrently with
  `scripts/rviz_ab_drive.py --enable-motion`; both publish `/cmd_vel`.
- FunASR `fsmn-vad` replaces RMS recording cutoff. Production defaults to
  Volcano bidirectional streaming ASR while 20 ms PCM capture also feeds FunVAD;
  `PROJECT_LINK_ASR_PROVIDER=faster_whisper` is an explicit manual fallback only.
  Keep both optional local models pre-downloaded on Orin and use a JetPack-compatible
  PyTorch build for FunASR.
- Voice motion is LLM-selected but Python-executed: ASR text goes to the
  official DeepSeek API for Tool Calling, the LLM chooses only whitelisted tools, and Python validates
  named map waypoints plus SLAM/TF readiness before creating a pending task.
- The LLM must never publish `/cmd_vel`, enable SO-101 torque, or call ROS
  actions directly. Motion/fetch tools require the local fixed safety summary
  and explicit `确认开始`; `停止`/`取消` bypasses the LLM and cancels active goals.
- Exception for demos only: `llm_motion_demo.launch.py` and
  `scripts/start_llm_voice_car_demo.sh` intentionally keep LLM Tool Calling and
  Volcano TTS while publishing bounded short `/cmd_vel` commands without SLAM.
  Do not use that mode as production navigation.
- Production named-waypoint motion uses `voice_nav2.launch.py` through
  `scripts/start_voice_nav2_stack.sh`. The voice node sends only confirmed Nav2
  `NavigateToPose` goals and must not create a production `/cmd_vel` publisher;
  `velocity_smoother` and `behavior_server` remain the allowed Nav2 publishers.
  `voice_direct_drive.launch.py` is retained only as an explicit supervised
  fallback.
- The fixed wake acknowledgement is a runtime-generated local MP3 at
  `~/.cache/project_link_voice/wakeup_ack.mp3`. It must finish playback before
  FunVAD recording begins. Serial wake matching uses a rolling binary buffer so
  split AIUI frames do not lose the `aiui_event` marker.
- `voice_dialog_node` now defaults to bounded continuous conversation: one wake
  opens multiple ASR/LLM/TTS turns, each next recording starts only after the
  current TTS generation finishes, and 8 seconds of follow-up silence returns to
  wake wait with the cached reply `好的，我退下了`. Explicit local exit words
  such as `停止`, `取消`, `退出`, `退下`, and `休息` bypass the LLM, cancel active
  motion/grasp/demo work, speak the same fixed reply, and end the session. A
  silence-only session timeout must not cancel an already running robot task.
- Stable voice USB identity is mandatory: udev maps WCH serial `0004` from
  `/dev/serial/by-id/usb-WCH.CN_USB_Single_Serial_0004-if00` to
  `/dev/project_link_wakeup`; PyAudio matches the iFlytek microphone by
  `XFM-DP-V0.0.18`; pygame uses PulseAudio sink
  `alsa_output.usb-C-Media_Electronics_Inc._USB_Audio_Device-00.analog-stereo`.
  Do not restore ttyACM, PyAudio-index, or ALSA-card-number defaults.
- Voice interactions use one `trace_id` across VAD, ASR, DeepSeek Tool Calling,
  Python tool execution, and Volcano TTS. Keep ordinary debug events in
  `~/.ros/project_link_voice/voice_debug.jsonl` and timing-only events in
  `voice_timing.jsonl`; new files are private mode `0600`. Timing console lines
  use `[VOICE_TIMING]`. Disable ordinary debug logging when recognized-text
  previews must not be persisted, without disabling timing diagnostics.
- DeepSeek requests force `thinking.type=disabled`. LLM text streams into the
  Volcano bidirectional TTS session; first PCM plays immediately, stale TTS
  generations are discarded, a cached `好的。` may play after 500 ms if the
  formal answer has not produced audio, and cache lookup is exact normalized
  text only. Do not merge or depend on the independent S2S branch.
- API secrets live only in `/home/wte/.config/project_link/voice_api.env`, which
  must be sourced before launch. Do not commit `DEEPSEEK_API_KEY`,
  `SILICONFLOW_API_KEY`,
  `VOLCANO_APP_ID`, `VOLCANO_ACCESS_TOKEN`, Feishu keys, weather keys, or model
  cache artifacts.

## Voice-To-Grasp Task Integration

- `voice_dialog_node` accepts fetch intent through LLM `fetch_item_from_location`
  tool calls. It maps the named waypoint to the configured Nav2 or direct-drive backend and maps spoken object
  aliases to YOLO-World targets, for example `药瓶=medicine bottle`.
- The fetch chain is only valid after the configured navigation Action succeeds and the
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
- End-effector ToF uses `project_link_vl53l0x` and
  `/visual_grasp/tof_range`. The Windows bench GUI and ROS node must never own
  the ESP32 serial port at the same time.
- `tof_enabled` and `tof_control_enabled` default false. Validate shadow mode
  first; enable control only after the mounted sensor's grasp distance is
  measured and `tof_calibrated=true` is explicitly set. In control mode stale/invalid range must hold approach motion and
  must not fall back to bounding-box-area closure.
- The moving SO-101 sensor is not a fixed child of the chassis xacro. Do not add
  a fake static TF; publish `so101_tof_link` until a real arm URDF/joint-state
  chain exists. Never add this end-effector Range topic to Nav2 costmaps.
- `tools/windows_visual_grasp_lab` is the non-ROS Windows hardware console. It
  directly owns the camera, SO-101 COM port, and ESP32 COM port during bench
  tests. Never run it concurrently with the old VisualTracker, the standalone
  VL53L0X Monitor, or the Orin ROS hardware nodes.
- The Windows console must remain fail-safe on startup: no automatic arm
  connection, no automatic torque, and no ToF control until calibration is
  explicitly confirmed. Keep the visible emergency stop/torque-off command.
- The Windows console is a supported direct-hardware mode, not only a temporary
  bench preview. Its refresh control and arm-torque control must remain in a
  vertical non-overlapping layout, and the joint page must remain scrollable.
- Windows SO-101 calibration must use the shared non-interactive sequence:
  start and disable torque, record the middle, sample every joint and gripper
  through the full safe range, finish/save, or cancel. Never call LeRobot's
  terminal-driven `robot.calibrate()` from the Qt process.
- During Windows calibration, disable normal connection, torque, gripper,
  presets, demonstration and automatic grasp controls. Keep the Windows field
  tutorial synchronized at
  `docs/WINDOWS_VISUAL_GRASP_INITIALIZATION_TUTORIAL.md`.
- Windows torque-off demonstration for visual-approach fitting must keep the
  selected YOLO target active and record flat time-series columns for frame
  size, expected center, bbox position/center/area, normalized errors,
  trusted/sequence state, ToF, and all six joints. Do not redesign the learned
  approach trajectory until the operator supplies a real visual demo CSV.
- LeRobot `SO101Follower.connect()` runs `configure()`, whose torque-disabled
  context re-enables torque on exit. The shared wrapper must immediately issue
  its own torque-off after connect and must not report a safe disconnected state
  until that step has been attempted.
- Feetech overheat/status errors must not stop the torque-off loop at the first
  motor. Retry each motor, send a no-response sync write of `Torque_Enable=0`,
  then read back the register when possible. A hardware-fault latch blocks
  torque enable and calibration until power-off, cooling and a clean reconnect.
- Serial shutdown must close the bus with `disable_torque=False` after the
  wrapper's own torque-off attempt. Never call the LeRobot disconnect path in a
  way that can skip `closePort()` because a motor status packet raised first.
- All SO-101 bus operations shared by Qt workers, status timers and control
  callbacks must use the wrapper's reentrant I/O lock. Read-only joint polling
  must be non-blocking and skip a cycle while another thread owns the bus.
- A simultaneous `Port is in use` result from every motor can be the Feetech
  SDK's in-process `port_handler.is_using` guard, not an external Windows COM
  owner. Only while holding the wrapper I/O lock, clear a stale true flag and
  retry torque-off once before escalating to physical power-off.
- A present LeRobot calibration file with mismatched motor EEPROM values is not
  automatically a request for a new physical calibration. With torque confirmed
  off, reapply the loaded file once and read it back. Enter `REQUIRED` only when
  no file exists or restore verification still fails, and report field-level
  offset/range differences.
- Calibration completion must write the motors, run configuration, restore
  torque-off, verify `is_calibrated`, and only then save/report `READY`.
- STS3215 `Present_Position` may use bit 15 as sign-magnitude when a homed joint
  crosses zero. Calibration sampling must decode that representation before
  tracking extrema. Recenter the completed range by adjusting the homing offset
  so every saved limit is within the model's `0..resolution-1` register range.
- Reject legacy or newly generated calibration files whose min/max limits fall
  outside the motor resolution. Values such as `32774` or `32952` are wrapped
  negative positions, not valid 12-bit limits, and cannot be silently clamped.
- Keep Windows preset permissions separate: recording requires connected,
  calibrated and confirmed torque-off; executing presets/manual arm commands
  requires connected, calibrated and torque-on. Never use one shared UI-ready
  predicate for both groups.
- Preset arrival feedback takes precedence over the timeout boundary. A true
  timeout must report per-joint remaining error in the Windows runtime log;
  controller `ERROR` transitions must never remain visible only in the status
  label. Automatic grasp must reject a pregrasp pose outside the visual-servo
  soft limit before commanding motion.
- A complete five-joint action must be written without a redundant pre-write
  observation. Read feedback only to fill genuinely partial commands and in the
  separate arrival-confirmation path; a transient final feedback failure must
  retry rather than convert an already completed move into `ERROR`.
- Presets must remain inside `preset_joint_limit`, default `+/-95` normalized.
  Values near `+/-100` are calibrated travel endpoints, not reliable operating
  poses. Reject them during recording and before motion instead of relaxing
  arrival tolerance enough to hide a physically unreachable joint target.
- Standby is the deliberate exception because it is an unloaded, supervised
  storage/initial pose. It may use `standby_joint_limit=99.5`; pregrasp and
  placement must continue using the stricter operational preset limit. Never
  generalize the standby exception to automatic visual-servo poses.
- Keep the general preset arrival tolerance strict at `2.0`, but allow the
  gravity-loaded elbow its separate default `elbow_arrive_threshold=5.0`.
  Expose both values in the Windows parameter panel; do not widen every joint
  merely to accommodate normal elbow compliance.
- LeRobot `SO101Follower.send_action()` is non-blocking and provides no arrival
  event. Preset completion must combine strict consecutive position checks with
  a bounded stable-near-target hysteresis; never treat a single `2.03 > 2.00`
  sample as proof of failure, and never use hysteresis to accept endpoint-scale
  errors.
- Keep the Windows JSONL debug mode available and enabled by default during
  field diagnosis. It must log config, presets, calibration, per-tick desired
  and normalized feedback, completion mode, and an error-time raw Feetech
  register snapshot. It must not record camera frames or model assets.
- Centering corrections may saturate at `joint_command_limit` without an
  immediate controller error. Clamp the command, log requested versus sent,
  and allow bounded target response time measured in fresh detector results.
  Abort only after the configured consecutive limit-hold cycles; approach
  motion remains fail-closed.
- The Windows console must keep one-click servo-center calibration. Map clicks
  from the scaled, letterboxed QLabel back into original frame pixels before
  deriving `center_offset_x/y`; save immediately and log the selected point.
- Visual-servo inputs must reject implausible YOLO center/area jumps and prefer
  association with the previous box. A detector result may drive at most one
  servo command; never let the faster GUI timer repeatedly command from a stale
  box. Centering must use a short robust error window, adaptive coarse/fine step
  limits, and consecutive centered confirmation. The current camera mount uses
  `tilt_direction=-1`. Do not restore frame-by-frame reactions to raw
  highest-confidence boxes.
- Visual-servo increments may need to accumulate to overcome SO-101 gravity and
  static friction. Keep the accumulated target bounded relative to current
  feedback through `centering_max_command_lead`; never repeatedly send the same
  ineffective tiny target, and never allow unbounded open-loop accumulation.
- Apply the same bounded-target rule to approach through
  `approach_max_command_lead`. ToF invalidity, joint limits, and total grasp
  timeout must still take precedence over accumulated motion.
- The first close-range handoff uses `FINAL_APPROACH`. Before handoff, YOLO is
  mandatory; after a bbox-size plus ToF near-field gate, YOLO loss is expected
  and final motion may use only valid ToF plus the bounded taught
  shoulder/elbow/wrist profile.
  Invalid ToF, final timeout, blind-travel limit, or joint soft limits must stop
  fail-closed. A large bbox alone must never authorize blind motion at far ToF.
  The current operator-selected final close threshold is
  `final_grasp_tof_m=0.090`; do not increase travel limits merely to reach it.
  After commanding the saved endpoint, allow only the bounded
  `final_approach_endpoint_settle_sec` feedback window before failing.
- Default centering must not use shoulder lift as a vertical image correction;
  that physical extension was mistaken for approach and drove the object out of
  frame. When tilt motion is disabled, use horizontal centering as the motion
  gate and treat the lower vertical target as a guide for the taught approach.
- `auto_lock_vertical_center_on_pregrasp` defaults false. A manual preview click
  must disable it immediately, clear any per-run lock, and persist the selected
  `center_offset_x/y`. If the operator explicitly enables auto lock, the first
  trusted bbox may define a temporary vertical reference plus the configurable
  lower `auto_lock_vertical_center_offset_ratio`.
  Repeated detector sequence numbers must not overwrite the last meaningful
  controller status. If visual handoff is enabled, reject grasp startup unless
  ToF enabled/control/calibrated gates are all true.
- Keep the Windows parameter pane vertically scrollable. Parameter spin boxes
  must ignore mouse-wheel changes so page scrolling cannot silently retune the
  arm. Preserve one-click restore-saved and recommended-profile controls; a
  recommended reset must not overwrite ports, model path, or servo-center offsets.
- Detection tracking is camera-only and must not move the arm. Automatic grasp
  owns the explicit sequence open gripper -> pregrasp -> centering -> approach
  -> grasp. Standby and placement remain explicit operator/orchestrator steps.
- Controller stop defaults to `IDLE`; only a deliberate motion-stop request may
  preserve the non-moving `TRACKING` state while the detector remains active.
  A normal stop must first send a complete current-joint hold command. Emergency
  stop skips that hold and disables torque immediately.
- Never restore the old fixed elbow polynomial used by visual approach. On the
  current calibration it converted a shoulder lift near `-80` into an elbow
  goal near `+83`, causing a dangerous downward strike. The current profile is
  targets the saved pregrasp shoulder endpoint plus the supervised demo deltas
  `lift +34`, `elbow +12.3`, `wrist -54`, while interpolating from actual
  feedback and enforcing a per-command jump gate. Do not shorten the endpoint
  merely because stable-near preset arrival stopped a few units early.
- Vertical click coordinates remain a visual/taught reference while shoulder
  tilt correction is disabled. Physical final-height tuning uses only the
  bounded `approach_profile_wrist_trim` in `-10..+10`; never silently map a
  large pixel Y error back onto shoulder lift.
- Preset motion must use a coordinated time-based trajectory for all five arm
  joints. Do not restore the old feedback-increment algorithm or defer pan until
  every other joint has extended; that causes visible jitter and unsafe shape
  changes before rotation.
- Visual centering deltas must be bounded per cycle. Approach must fail closed
  at the configured normalized joint soft limit or grasp timeout instead of
  continuing toward the mechanical endpoint when box/ToF closure never occurs.
- Successful recalibration invalidates all recorded presets. Clear runtime poses
  and require standby/pregrasp/placement to be recorded again under the new
  normalization.
- The current legacy VisualTracker venv may point to an uninstalled Python 3.12
  runtime. The Windows launcher must test candidate interpreters before use and
  skip broken environments. Rebuild the repository `.venv` before claiming the
  offscreen GUI or real hardware has been validated on Windows.
- Before torque, manual approach, or `TrackAndGrasp` action tests, verify a clear
  workspace and physical E-stop/power-cut procedure. The scheduler action is
  only valid after navigation reaches a safe manipulation pose.
- SO-101 connection must use `connect(calibrate=False)` and fail clearly when
  calibration is missing. Never let the headless Orin node block on LeRobot's
  interactive `input()` calibration. Remote calibration is a four-step ROS
  flow: start/disable torque, record middle, record full ranges, finish/save.
- During calibration, support the arm by hand, do not issue grasp/preset
  commands, and require every joint including the gripper to move through a
  safe nonzero range. The field runbook is
  `docs/SO101_VISUAL_GRASP_INITIALIZATION_TUTORIAL.md`.
