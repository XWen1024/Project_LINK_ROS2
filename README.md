# Project LINK ROS2

Project LINK / Lingxi is a ROS 2 based eldercare mobile manipulation robot
prototype. The long-term MVP is a complete task loop:

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

The current engineering phase uses the verified Point-LIO + slam_toolbox + Nav2
stack as the base for **BU03/BU04 UWB summon and person-following**. UWB produces
the person's relative position; Project LINK transforms it into `map`, applies a
bounded standoff policy, and submits only high-level Nav2 goals. It never becomes
another `/cmd_vel` publisher.

An isolated Volcengine end-to-end voice integration is also available on the
`codex/volc-s2s-voice-integration` branch. It keeps the iFlytek local wake event,
USB microphone, FunVAD hard endpoint, and USB speaker, while a persistent native
ARM64 process owns the official Embedded Kit low-load WebSocket S2S connection.
The first launch is pure voice only: it starts no chassis process and publishes
no `/cmd_vel`.

```bash
cd /home/wte/wheeltec_robot-volc-voice
./experiments/volc_s2s_smoke/scripts/build.sh
source /opt/ros/humble/setup.bash
colcon build --packages-up-to project_link_voice
./scripts/start_volc_s2s_voice.sh --restart --no-attach
```

Detailed setup, timing phases, and rollback instructions are in
`docs/VOLCENGINE_END_TO_END_VOICE_MIGRATION_HANDOFF.md`. The first Orin field
bring-up report is `docs/VOLC_S2S_VOICE_INTEGRATION_REPORT.md`.

## Current Status

- Date: 2026-08-04.
- Main onboard computer: Jetson Orin Nano.
- Orin workspace: `/home/wte/wheeltec_robot`.
- Old Orin workspace backup: `/home/wte/wheeltec_robot_backup_20260627_1250`.
- Remote repository: `git@github.com:XWen1024/Project_LINK_ROS2.git`.
- The migrated workspace builds successfully with `colcon build --symlink-install`.
- C63A base serial link is confirmed on `/dev/wheeltec_controller` at `115200`;
  `/odom`, `/imu/data_raw`, and `/PowerVoltage` return at about `20 Hz` after the
  C63A board is healthy.
- Known-good SLAM fallback: `rf2o + EKF + slam_toolbox`.
- Current priority: validate UWB serial identity, installed coordinate axes and
  mounting calibration in shadow mode, then execute supervised low-speed summon
  and follow fault tests through the known-good Nav2 stack. The repository ships
  with live UWB motion locked and an intentionally invalid calibration.
- BU04 has separate physical data paths. The Type-C port marked `USB` is STM32
  CDC `0483:5740` and is the verified measurement stream; the Type-C port marked
  `TTL` is CH340 `1a86:7523` and is reserved for AT/configuration. Windows native
  USB produced 289/289 valid framed JSON messages in 10 seconds (`28.9 Hz`). The
  repository maps these to `/dev/uwb-bu04` and `/dev/uwb-bu04-at` respectively.
  The saved PDoA base ID has also been repaired from `65535` to `1`. No firmware
  reflash is needed. Orin also parsed 288/288 valid frames from native USB
  `/dev/ttyACM1` in 10 seconds (`28.8 Hz`); the next gate is installing the stable
  alias through the GitHub update flow and running ROS shadow validation.

## Repository Layout

```text
AGENTS.md             Codex/agent operating notes and current project truth
PROGRESS.md           Current project status, progress, and roadmap notes
README.md             This quick-start guide
bringup/              Top-level integration launch notes
configs/              Runtime configuration files
docs/                 Project notes and design documents
maps/                 Curated maps only, not generated logs
robot_description/    URDF, meshes, and robot model launch files
scripts/              Utility scripts
src/                  ROS 2 packages owned by this workspace
```

Important handoff document:

```text
docs/C63A_BASE_AND_SLAM_HANDOFF.md
docs/SITE_VOICE_MOBILE_MANIPULATION_RUNBOOK.md
docs/NAVIGATION_TWO_HANDOFF.md
docs/UWB_SUMMON_AND_FOLLOW_HANDOFF.md
```

Important ROS 2 packages:

```text
turn_on_wheeltec_robot        Base serial, robot description, lidar, EKF, SLAM launch
wheeltec_nav2                 Nav2 launch/config package; not used for direct A/B
wheeltec_slam_toolbox         slam_toolbox configs and wrappers
rf2o_laser_odometry           Laser-scan odometry
serial                        Vendored serial library
wheeltec_robot_msg            Custom Wheeltec messages
project_link_uwb_interfaces   UWB observation and summon/follow Action interfaces
project_link_uwb_navigation   BU04 parser, calibration, Nav2 bridge, optional MCP
```

## UWB Summon And Follow

Build the new packages on Orin:

```bash
cd /home/wte/wheeltec_robot
source /opt/ros/humble/setup.bash
colcon build --packages-select \
  project_link_uwb_interfaces project_link_uwb_navigation
source install/setup.bash
```

The current Orin has `setuptools 82`, while Humble's colcon invokes the removed
`setup.py develop --editable` path for ament Python packages under
`--symlink-install`. Use the normal install build above for these UWB packages.

Start Navigation Two first, then UWB shadow mode:

```bash
./navigation_two_start_navigation.sh --restart
export PROJECT_LINK_UWB_TAG_ADDRESS='<private-a16>'
./navigation_two_start_uwb.sh --shadow \
  --device /dev/uwb-bu04 \
  --params ~/.config/project_link/uwb_navigation.yaml \
  --restart
./navigation_two_uwb.sh summon
# The follow command remains attached for feedback; stop it from another shell.
./navigation_two_uwb.sh follow
./navigation_two_uwb.sh stop
```

For supervised single-tag calibration after a BU04 power cycle, the durable
shadow-only helper discovers exactly one private tag without printing or saving
its address:

```bash
python3 scripts/start_uwb_shadow_auto_tag.py \
  --device /dev/ttyACM1 --restart
```

This helper cannot enable live motion. Live mode still requires an explicitly
supplied private tag, operator confirmation, and an approved calibration file.
Standalone shadow does not require the chassis, lidar, SLAM, map, or Nav2 and is
therefore the preferred four-direction raw UWB calibration mode. Summon/follow
map goals still require a healthy `map -> base_footprint` transform.

Shadow mode publishes `/uwb_navigation/proposed_goal` but sends no Nav2 goal.
Live operation is documented and gated in
`docs/UWB_SUMMON_AND_FOLLOW_HANDOFF.md`; do not enable it before measured
four-direction calibration and stop/takeover tests.

Summon and follow intentionally have different Nav2 goal lifecycles. A summon
computes and submits one static goal, then waits for arrival or a fail-closed
fault. Once submitted, later summon observations are used only for freshness and
range-arrival safety, not moving-target speed estimation or goal replacement.
Follow alone may validate target speed, throttle, cancel, and replace rolling
goals as the person moves.

The launcher passes the private tag and exact device through tmux session
environment, then waits for a real observation. Nav2's own `velocity_smoother`
and `behavior_server` are accepted motion-path publishers; any unrelated
publisher still rejects live UWB goals. The launch file explicitly preserves a
digits-only private tag address as a string, as required by the decoder. The
Action Goal carries only summon/follow mode; the configured observation source
remains enforced inside the local ROS safety layer.

The UWB launch and operator commands default to `rmw_cyclonedds_cpp` to avoid a
Humble Fast DDS custom-Action history allocation defect on the current Orin.
Navigation Two remains on Fast DDS and interoperates over DDS domain 42.

## Build On Orin

```bash
cd /home/wte/wheeltec_robot
source /opt/ros/humble/setup.bash
colcon build --symlink-install
source install/setup.bash
```

Expected packages:

```bash
ros2 pkg list | grep -E 'turn_on_wheeltec_robot|wheeltec_nav2|wheeltec_slam_toolbox|rf2o_laser_odometry|wheeltec_robot_msg|project_link_uwb|serial'
ros2 pkg executables turn_on_wheeltec_robot
ros2 pkg executables rf2o_laser_odometry
```

For project network defaults on Orin:

```bash
source scripts/project_link_env.sh
```

This script intentionally does not modify `~/.bashrc`.

## Ubuntu RViz2 Visualization

Use the same ROS 2 domain on Orin and the Ubuntu visualization computer.

On the Ubuntu computer:

```bash
export ROS_DOMAIN_ID=42
export ROS_LOCALHOST_ONLY=0
source /opt/ros/humble/setup.bash
rviz2
```

In RViz2, start with:

- `Fixed Frame`: `base_footprint` before SLAM, then `map` after SLAM is running
- `TF`
- `RobotModel`: `Description Source` = `Topic`, `Description Topic` =
  `/robot_description`
- `LaserScan`, topic `/scan`
- `Map`, topic `/map`
- `Odometry`, topic `/odom_rf2o`, `/odometry/filtered`, or `/odom_lio`

Optional DDS discovery test:

```bash
# Ubuntu computer
ros2 multicast receive

# Orin
ros2 multicast send
```

## SLAM-First Bringup

Do not start Nav2 for this phase. Bring the system up in layers.

### One-Command tmux Bringup

On Orin, the preferred current entrypoint is the root tmux script:

```bash
cd /home/wte/wheeltec_robot
./start_slam_tmux.sh --restart
```

It creates tmux session `project_link_slam` with:

- `base`: C63A base serial node, publishing `/odom`, `/imu/data_raw`, and
  `/PowerVoltage`
- `lidar`: Unitree L1 / UniLidar driver on `/dev/unilidar`
- `robot`: split panes for `unilidar_p2s.launch.py` and
  `robot_mode_description.launch.py`
- `slam`: current `rf2o_slam_toolbox.launch.py`
- `check`: live topic/TF monitor for C63A base topics, `/scan`, `/odom_rf2o`,
  `/odometry/filtered`, `/map`, `odom -> base_footprint`, and `map -> odom`

`./start_slam_tmux.sh --restart` now waits for real `/odom` and `/scan` messages
before starting the SLAM window. For lidar-only debugging, use:

```bash
./start_slam_tmux.sh --restart --no-base
```

Useful tmux controls:

```text
Ctrl-b n              next window
Ctrl-b p              previous window
Ctrl-b 0..9           jump to window number
Ctrl-b w              choose a window from the list
Ctrl-b arrow          move between panes
Ctrl-b d              detach, keep everything running
tmux attach -t project_link_slam
tmux kill-session -t project_link_slam
```

Manual bringup is still useful for debugging individual layers:

1. Start the C63A base serial node and confirm base return data:

   ```bash
   ros2 launch turn_on_wheeltec_robot base_serial.launch.py
   ros2 topic hz /odom
   ros2 topic hz /imu/data_raw
   ros2 topic echo --once /PowerVoltage
   ```

2. Start the robot description TF:

   ```bash
   ros2 launch turn_on_wheeltec_robot robot_mode_description.launch.py
   ```

3. Start lidar and confirm `/scan`.

   For Unitree L1 / UniLidar, the vendor SDK stays outside this repository. Start
   the external driver so it publishes `/unilidar/cloud`, then run:

   ```bash
   ros2 launch turn_on_wheeltec_robot unilidar_p2s.launch.py
   ros2 topic hz /unilidar/cloud
   ros2 topic hz /scan
   ```

   For the Wheeltec-configured serial lidar:

   ```bash
   ros2 launch turn_on_wheeltec_robot wheeltec_lidar.launch.py
   ros2 topic hz /scan
   ```

4. Start the current SLAM candidate:

   ```bash
   ros2 launch turn_on_wheeltec_robot rf2o_slam_toolbox.launch.py
   ```

5. Verify topics and TF:

   ```bash
   ros2 topic hz /odom
   ros2 topic hz /odom_rf2o
   ros2 topic hz /odometry/filtered
   ros2 topic hz /map
   ros2 run tf2_ros tf2_echo odom base_footprint
   ros2 run tf2_ros tf2_echo map odom
   ```

Current intended flow:

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

## Direct RViz A-To-B Check

This test intentionally does not use Nav2. It keeps the current SLAM stack
running as the pose source, then uses two RViz clicked points and a tiny direct
controller to publish `/cmd_vel`.

1. Start the known-good SLAM route:

   ```bash
   cd /home/wte/wheeltec_robot
   ./start_slam_tmux.sh --restart
   ```

2. In RViz, confirm `/map`, `/scan`, TF, and robot model look sane.

3. Start the direct A/B driver on Orin:

   ```bash
   source /home/wte/wheeltec_robot/scripts/project_link_env.sh
   python3 /home/wte/wheeltec_robot/scripts/rviz_ab_drive.py --enable-motion
   ```

4. In RViz, set `Fixed Frame` to `map`, select the `Publish Point` tool, and
   click:
   - A: where the robot currently is, used as a sanity check.
   - B: a very nearby target point.

Before sending a real point pair, verify:

   ```bash
   ros2 topic hz /scan
   ros2 topic hz /odom
   ros2 topic hz /odometry/filtered
   ros2 topic echo --once /cmd_vel
   ros2 run tf2_ros tf2_echo map odom
   ros2 run tf2_ros tf2_echo odom base_footprint
   ```

This script does no planning and no obstacle avoidance. Keep the first B point
close, with the wheels lifted or a person at the E-stop.

## Point-LIO Bringup

The active route is Point-LIO as 3D lidar odometry. Keep the Point-LIO source
outside this repository for now:

```bash
cd /home/wte
mkdir -p point_lio_ws/src
cd point_lio_ws/src
git clone --recursive https://github.com/dfloreaa/point_lio_ros2.git point_lio
cd /home/wte/point_lio_ws
source /opt/ros/humble/setup.bash
rosdep install --from-paths src --ignore-src -r -y
colcon build --symlink-install --packages-select point_lio
```

The current Orin already has this external workspace built at `/home/wte/point_lio_ws`.
The project environment script sources `/home/wte/point_lio_ws/install/setup.bash`
when it exists.

Phase A, Point-LIO odometry only:

```bash
cd /home/wte/wheeltec_robot
./start_point_lio_tmux.sh --restart
```

Phase A does not start `/scan`, `slam_toolbox`, or `/map`. It preserves raw 3D
Point-LIO state while publishing a separate planar base pose:

```text
/unilidar/cloud + /unilidar/imu
-> Point-LIO /odom_lio_raw and lio_odom -> lio_base
-> lio_planar_projection
-> /odom_lio and odom -> base_footprint
```

The Phase A topic checks are:

```bash
ros2 topic hz /unilidar/cloud
ros2 topic hz /unilidar/imu
ros2 topic hz /odom_lio_raw
ros2 topic hz /odom_lio
ros2 topic hz /point_lio/cloud_registered
ros2 run tf2_ros tf2_echo lio_odom lio_base
ros2 run tf2_ros tf2_echo odom base_footprint
```

During the stationary test, `odom -> base_footprint` must retain `z=0` with
roll and pitch near zero. Tune the versioned
`configs/point_lio/lio_planar_projection.yaml` only after a stationary and
straight-line chassis check.

The corrected Phase A mounting/projection baseline was verified on 2026-08-03:
cloud is about `9.5 Hz`, IMU about `247 Hz`, and raw/projected odom plus
registered cloud are at lidar cadence. The installed sensor correction is roll
`3.14159`, pitch `0.0`, yaw `2.0112063268`. Unitree's factory LiDAR/IMU axes are
parallel, so Point-LIO retains identity `extrinsic_R` and the factory
millimetre-scale translation. The corrected planar transform reduced the false
lever arm from about `0.94 m` to about `0.20 m`, matching the real `0.19 m`
forward mounting. Stationary jitter is centimetre-scale; supervised 90-degree
in-place-turn and straight-line tests both matched the physical chassis motion.

Phase B, Point-LIO odometry plus `slam_toolbox` 2D map, is blocked until the
Phase A checks above pass:

```bash
cd /home/wte/wheeltec_robot
./start_point_lio_tmux.sh --restart --with-2d-map
```

The canonical-URDF Phase B bringup was verified on Orin on 2026-08-03: `/scan`
runs at about `9.34 Hz`, `/odom_lio` at about `9.31 Hz`, `/map` publishes, and
`map -> odom -> base_footprint` is continuous. No separate lidar static-TF node
is present.

The raw height-sliced scan is intentionally sparse: a measured frame averaged
about `129/723` valid bins (`17.8%`) in roughly 57 disconnected segments, while
the union of 30 frames covered about `46.1%`. This explains why RViz with LaserScan
Decay Time 3 showed a clean outline while slam_toolbox produced an unrelated map:
RViz was overlaying many TF-compensated frames, but slam_toolbox received each
fragment separately. Phase B now keeps `/scan` for inspection and feeds
slam_toolbox from `/scan_accumulated`, produced by a 3-second rolling,
TF-compensated accumulator with 4 cm spatial deduplication and a 25% valid-bin
startup threshold. Tune `config/laser_scan_accumulator.yaml` before changing
slam_toolbox matching parameters.

The first Orin validation of this route warmed up at `25.3%` valid-bin coverage,
then stabilized around `29%` while stationary. `/scan_accumulated` published at
about `9.49 Hz`, and slam_toolbox registered it as its laser sensor. The stack is
therefore ready for the next supervised in-place-turn/map visual comparison.

A subsequent user-driven lap produced a usable structural map: the major walls,
rooms, and corridor boundaries are coherent. Small edge speckles remain around
cluttered areas, but they are acceptable for a first conservative Nav2 costmap
test and do not justify retuning Point-LIO before navigation bringup.

The tmux session is `project_link_point_lio` and contains:

- `lidar`: Unitree L1 / UniLidar driver
- `robot`: robot description, `/scan` conversion, and `/scan_accumulated` when
  `--with-2d-map` is used
- `lio`: `point_lio_unilidar_l1.launch.py`
- `check`: live monitor for `/unilidar/cloud`, `/unilidar/imu`,
  `/odom_lio_raw`, `/odom_lio`, `/point_lio/cloud_registered`, `/scan`,
  `/scan_accumulated`, `/map`,
  and the raw/projected TF chain

Manual Point-LIO launch requires the canonical robot description in a separate
terminal:

```bash
cd /home/wte/wheeltec_robot
source scripts/project_link_env.sh
ros2 launch turn_on_wheeltec_robot robot_mode_description.launch.py
```

```bash
cd /home/wte/wheeltec_robot
source scripts/project_link_env.sh
ros2 launch turn_on_wheeltec_robot point_lio_unilidar_l1.launch.py
```

### Nav2 against the live Point-LIO map

The maintained operational handoff is
`docs/NAVIGATION_TWO_HANDOFF.md`. The command-only quick reference is
`docs/NAVIGATION_TWO_COMMANDS.md`.

Repository-root convenience scripts provide the normal field workflow:

```bash
./navigation_two_start_mapping.sh --restart
./navigation_two_start_navigation.sh --restart
./navigation_two_save_map.sh --name site_map
./navigation_two_status.sh
./navigation_two_stop.sh
```

The mapping entrypoint stops Nav2 before keyboard teleop can be used. The full
navigation entrypoint starts/reuses C63A, Point-LIO Phase B, slam_toolbox, and
Nav2 in dependency order, but sends no goal or nonzero velocity. The save helper
uses a separate tmux session and saves both occupancy output and a best-effort
slam_toolbox posegraph.

The startup topic gates retry ROS graph discovery until their deadline. This
prevents normal USB and driver startup latency from causing an immediate false
failure before `/odom`, Unitree data, or Point-LIO topics publish their types.

With Phase B still running, start only the Nav2 planning/control stack:

```bash
cd /home/wte/wheeltec_robot
./start_point_lio_nav2_tmux.sh --restart
```

This does not start AMCL, map_server, another slam_toolbox, or another odometry
publisher. It consumes the live `/map`, `/odom_lio`, `/scan_accumulated`, and
`map -> odom -> base_footprint` chain. Initial limits are `0.18 m/s` linear and
`0.50 rad/s` path-following angular velocity with the conservative measured
`0.51 x 0.41 m` chassis footprint.

Stop `scripts/c63_keyboard_teleop.sh` before starting this wrapper: keyboard
teleop publishes zero `/cmd_vel` messages continuously and would fight Nav2.
The wrapper starts no goal and sends no nonzero velocity by itself. Inspect the
local/global costmaps first; use RViz `2D Goal Pose` only with a clear test area
and a physical E-stop ready.

The first Orin bringup reached `active` for all managed Nav2 nodes. DWB, NavFn,
the local/global costmaps, recovery behaviors, BT Navigator, waypoint follower,
and velocity smoother loaded successfully; `/navigate_to_pose` is available.
The global costmap accepted the live map at `158 x 174` cells and `0.05 m/pixel`.
A four-second idle check observed no `/cmd_vel` message, so no goal was executed.

If the local costmap is entirely white, first check timestamp lag rather than
changing its resolution. One long-running Point-LIO session fell about `21 s`
behind the live scan; restarting Phase B restored LIO lag to about `0.03 s`. Its
map was preserved at
`/home/wte/maps/point_lio_nav2_pre_restart_20260803_2312.yaml` before restart.

This lag is a throughput backlog, not a network delay or a fixed clock offset:
the lidar supplied about `9.6 Hz` while delayed Point-LIO output had fallen to
about `8.5 Hz`, so an unbounded queue grew every second. The repository now has
two controls for it. Phase B starts Point-LIO in `odom_only` mode with point
filter `2`, `0.15 m` surface/map voxels, a `150 m` local cube, and `40 m`
detection range. The external source patch then caps the LiDAR/IMU queues and
ROS QoS depths so overload drops stale input instead of increasing latency.

After updating the Orin repository, apply and build the external-source change:

```bash
cd /home/wte/wheeltec_robot
./scripts/apply_point_lio_realtime_patch.sh --check
./scripts/apply_point_lio_realtime_patch.sh

cd /home/wte/point_lio_ws
source /opt/ros/humble/setup.bash
colcon build --symlink-install --packages-select point_lio
```

The patch script preserves unrelated changes in the external dirty checkout and
is safe to run again after it has been applied. Normal Phase B no longer expects
`/point_lio/cloud_registered`; use Phase A or set `POINT_LIO_ODOM_ONLY=false`
only when deliberate 3D RViz output is needed.

The local window is now `3 x 3 m` (`60 x 60` cells at `0.05 m`). The obstacle
source explicitly accepts `-0.1..2.0 m` height so scans expressed from elevated
`base_link` are not filtered out. The first verified frame contained 69 lethal
and 986 inflated cells, confirming that local obstacle marking is active.

The chassis outer envelope was subsequently measured as approximately `0.51 m`
long, `0.41 m` wide, and `0.82 m` high. The previous model was only
`0.40 x 0.35 m`. URDF and both costmaps now use the measured length/width, with
`0.01 m` padding per side for an effective `0.53 x 0.43 m` collision envelope.

The current behavior tree validates the active path at 2 Hz and replans only
when that path becomes invalid or the goal changes. This keeps DWB from chasing
small one-hertz path changes caused by an expanding online map. Recovery clears
both costmaps, attempts a collision-checked `360 degree` scan spin, clears again,
and replans; reverse recovery is intentionally absent
because the robot has no reliable rear obstacle view. Controller and velocity
smoother linear minimums are both zero, so this configuration cannot request
reverse motion.

Nav2 pose and velocity intentionally come from different sources. Pose is still
the Point-LIO-owned `map -> odom -> base_footprint` TF, but DWB and the behavior
tree read chassis-frame velocity from C63A `/odom`. Do not use `/odom_lio` as the
controller velocity source: Point-LIO's state velocity is expressed in its world
frame and the planar adapter previously copied it directly into an Odometry
child-frame twist. A stationary sample consequently reported about `0.020 m/s`
forward and `-0.037 m/s` lateral motion. The differential controller now uses
C63A twist and clamps lateral velocity noise because the base is nonholonomic.
Start `base_serial.launch.py` before the Nav2-only wrapper; the wrapper waits for
a real `/odom` message and otherwise refuses to bring up the controller.

To discourage wall shortcuts without closing usable gray corridors, local and
global inflation use a `0.40 m` radius and `3.5` cost scaling. DWB uses the more
permissive `BaseObstacle` critic, looks farther ahead for path alignment, and
gives more weight to geometric path distance than to small grid-cell heading
changes. The collision-checked smoother was removed after it repeatedly rejected
otherwise usable NavFn paths.

Goal completion is intentionally practical for this low-speed prototype:
`0.25 m` position tolerance and `0.50 rad` heading tolerance. This prevents the
path from remaining active while the robot repeatedly corrects the last few
centimeters or degrees. Recovery is limited to one collision-checked full scan
turn, with a 20-second allowance; it cannot perform a second automatic circle or
reverse the robot.

`robot_state_publisher` expands the package xacro and is the only sensor static
TF authority. Neither Point-LIO nor `unilidar_p2s.launch.py` publishes duplicate
sensor transforms.

The tmux script waits for real `/unilidar/cloud` and `/unilidar/imu` messages
before starting Point-LIO. This avoids judging the stack while the lidar driver is
visible in the ROS graph but has not produced data yet.

The single authoritative model is
`src/turn_on_wheeltec_robot/urdf/patrol_robot.urdf.xacro`. It contains the
verified mounting pose, driver-frame correction, and Unitree factory IMU origin
offset. Rebuild the package after changing it.

Do not run `rf2o_slam_toolbox.launch.py` at the same time as the Point-LIO launch.
Only one node stack should publish `odom -> base_footprint`. In the Point-LIO
stack, the raw node owns `lio_odom -> lio_base` and `lio_planar_projection`
owns the projected base TF.

## Hardware Test Strategy

- Default Orin SSH target: `wte@orin`. Example: `ssh wte@orin`.
- Lidar-only check: connect only lidar and validate `/scan` plus RViz2 display.
- Lidar SLAM check: connect lidar, publish robot description TF, then run the
  current SLAM launch. This can test whether laser odometry is viable.
- SLAM-only check: connect lidar plus STM32 base controller so `/odom` is
  available. Keep Nav2 stopped and do not publish `/cmd_vel` during that check.
- C63A base handoff and keyboard teleop details are in
  `docs/C63A_BASE_AND_SLAM_HANDOFF.md`.
- Differential keyboard teleop helper:
  `scripts/ssh_c63_keyboard_teleop.ps1` starts `/tmp/c63_keyboard_teleop.sh` on
  `wte@orin` over SSH by default. It publishes `/cmd_vel`, uses dead-man
  behavior, and exits with a stop command.
- Safety default: lift the wheels, disconnect motor power, or keep a person ready
  at the E-stop before any command that could move the base.

## External Dependencies

Unitree L1 / UniLidar SDK is kept outside this repository for now.

Recommended Orin location:

```text
/home/wte/unilidar_sdk
```

This repository should store integration launch/config files, but not vendor build
outputs.

## Git Hygiene

Generated ROS 2 workspace folders are intentionally ignored:

```text
build/
install/
log/
```

Keep hardware logs, rosbag recordings, generated maps, and temporary build
artifacts out of Git unless a small sample is intentionally added for documentation
or tests.

Repository updates should flow through Git, not direct file replacement on Orin:

```bash
# Local Windows repository
git add <changed-files>
git commit -m "<short message>"
git push origin main

# Orin
cd /home/wte/wheeltec_robot
git pull --ff-only
```

Use commits as rollback points. For launch files, hardware scripts, configs, and
procedure-changing documentation, smaller commits are preferred over one large
mixed change. Avoid `scp` or other direct overwrite methods for repository files
unless the user explicitly approves an emergency exception.

## Guarded Voice Direct Drive (Experimental)

`project_link_voice` and `project_link_voice_interfaces` add a production-oriented
voice path for the current SLAM-first milestone. It is **not Nav2** and has no
path planning or obstacle avoidance.

For the fastest site procedure, use
`docs/SITE_VOICE_MOBILE_MANIPULATION_RUNBOOK.md`. It covers one-command map
capture, voice waypoint saving, iFlytek wake testing, USB speaker checks, direct
drive enablement, and visual-grasp bringup.

### Production Voice With Navigation2

Start Navigation Two plus the production ASR/DeepSeek/TTS voice node in safe
dry-run mode:

```bash
cd /home/wte/wheeltec_robot
bash scripts/start_voice_nav2_stack.sh --restart
```

After named waypoints, Nav2 costmaps, TF, the physical E-stop, and the test area
have been checked, allow confirmed navigation goals:

```bash
bash scripts/start_voice_nav2_stack.sh --restart --enable-motion
```

Only after visual grasp passes its independent safety checks:

```bash
bash scripts/start_voice_nav2_stack.sh --restart --with-visual \
  --enable-motion --enable-visual-grasp
```

The voice node never publishes production `/cmd_vel` in Nav2 mode. A named
waypoint task requires `确认开始`; `停止` or `取消` bypasses the LLM and cancels the
active Nav2 and grasp goals. The wake response is cached locally at
`~/.cache/project_link_voice/wakeup_ack.mp3` and finishes before recording.

### Standalone LLM Voice Car Demo

If the site has no lidar/map/arm setup available, use the standalone LLM voice
car demo. It starts the base serial node plus a dedicated voice node, does not
use SLAM, does not use waypoints, and exposes only two demo topics:
`/voice_demo/text_input` for text tests and `/voice_demo/status` for status.
Motion still goes to the normal `/cmd_vel`.

```bash
cd /home/wte/wheeltec_robot
bash scripts/start_llm_voice_car_demo.sh --restart
```

This path uses the official DeepSeek API with `deepseek-v4-flash` when
`DEEPSEEK_API_KEY` is set. The LLM can only call `demo_motion` with `forward`,
`backward`, `turn_left`,
`turn_right`, `spin`, or `stop`. If the LLM is unavailable, it falls back to the
same local words for emergency demos.

Scan serial, microphone, speaker, and cloud/TTS env state:

```bash
bash scripts/start_llm_voice_car_demo.sh --scan-only
```

If auto scan picks the wrong iFlytek serial port or microphone:

```bash
bash scripts/start_llm_voice_car_demo.sh --restart --wakeup-port /dev/ttyUSB0 --audio-input-index 2
```

Text test without microphone:

```bash
ros2 topic pub --once /voice_demo/text_input std_msgs/msg/String "data: '请你往前走两步'"
ros2 topic pub --once /voice_demo/text_input std_msgs/msg/String "data: '帮我转个圈'"
ros2 topic pub --once /voice_demo/text_input std_msgs/msg/String "data: '停止'"
```

This mode publishes `/cmd_vel` directly for short bounded durations. Keep the
robot clear, low speed, and physically ready to E-stop. Set the USB speaker as
the OS default audio output; Volcano TTS uses the default pygame output device.

Every microphone or `/voice_demo/text_input` request has a `trace_id`.
Per-stage timing is printed with `[VOICE_TIMING]` and persisted to
`~/.ros/project_link_voice/voice_timing.jsonl`; ordinary state/debug events go
to `~/.ros/project_link_voice/voice_debug.jsonl`. The measured phases include
FunVAD recording, faster-whisper ASR, DeepSeek API/response parsing, Python tool
execution, TTS dispatch, first audio, synthesis completion, and total latency.

The path is:

```text
serial wake event -> FunASR fsmn-vad endpointing -> faster-whisper
-> DeepSeek official LLM Tool Calling
-> Python safety validation and spoken confirmation
-> Nav2 /navigate_to_pose in production, or /voice/drive_to_point as fallback
-> optional /visual_grasp/track_and_grasp after arrival
```

Safety rules:

- `enable_motion:=false` is the default. In this mode confirmations are dry-run
  only and the voice node sends no navigation goal.
- The LLM may choose a whitelisted tool and fill arguments, but Python validates
  every tool call. It never lets the LLM publish `/cmd_vel`, enable torque, or
  call ROS actions directly.
- Motion and fetch tools only create a pending task. `确认开始` is mandatory after
  the Python safety summary; `停止` or `取消` bypasses the LLM and cancels the
  active Action immediately.
- Voice commands only accept saved `map` waypoints. Free-form coordinates from
  speech or LLM output are rejected.
- In Nav2 mode the voice node does not publish `/cmd_vel`; cancellation goes
  through `NavigateToPose`. A physical E-stop is still mandatory.
- The direct-drive fallback stops on cancel, TF loss, timeout, watchdog expiry,
  shutdown, or completion. Do not run it beside `rviz_ab_drive.py` motion.

### Orin setup

```bash
cd /home/wte/wheeltec_robot
source /opt/ros/humble/setup.bash
python3 -m venv --system-site-packages .venv-voice
source .venv-voice/bin/activate
# First install the JetPack/CUDA-compatible PyTorch wheel for this Orin.
pip install -r src/project_link_voice/requirements-orin.txt
colcon build --symlink-install --packages-select project_link_voice_interfaces project_link_voice
source scripts/project_link_env.sh
```

Cloud API secrets are loaded only from the Orin private environment file:

```bash
mkdir -p /home/wte/.config/project_link
nano /home/wte/.config/project_link/voice_api.env
chmod 600 /home/wte/.config/project_link/voice_api.env
```

Required values:

```bash
export DEEPSEEK_API_KEY=...
export VOLCANO_APP_ID=...
export VOLCANO_ACCESS_TOKEN=...
export VOLCANO_RESOURCE_ID=seed-tts-2.0
export VOLCANO_SPEAKER=...
export QWEATHER_API_KEY=...   # optional; weather tool only
```

Source it before launching voice:

```bash
source /home/wte/wheeltec_robot/scripts/project_link_env.sh
source /home/wte/.config/project_link/voice_api.env
source /home/wte/wheeltec_robot/install/setup.bash
```

Pre-download models while the Orin has network access, then preserve the model
cache for offline operation:

```bash
python3 -c "from funasr import AutoModel; AutoModel(model='fsmn-vad', device='cuda')"
python3 -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cuda', compute_type='float16')"
```

Bring up the known-good SLAM stack first. Verify `/map`, `/scan`, `/odom`, and
`map -> base_footprint`, then start dry-run voice integration:

```bash
./start_slam_tmux.sh --restart
source scripts/project_link_env.sh
ros2 launch project_link_voice voice_direct_drive.launch.py
```

To run only the voice service on a local test machine, without SLAM, direct
drive, or `/cmd_vel`, use the pure test launch. It defaults to `COM9`,
`115200`, and `wakeup_only:=true`, so it prints all serial frames and reports
only `aiui_event` frames as wakeups:

```bash
ros2 launch project_link_voice voice_pure_test.launch.py
```

Useful overrides:

```bash
ros2 launch project_link_voice voice_pure_test.launch.py wakeup_serial_port:=COM9
ros2 launch project_link_voice voice_pure_test.launch.py wakeup_match_text:=
ros2 launch project_link_voice voice_pure_test.launch.py wakeup_only:=false
```

`voice_direct_drive.launch.py` also has `pure_test_mode:=auto` in its default
parameter file. If it starts and sees no `/map`, `/scan`, or `/odom` topics, it
prints a console banner and stays in non-motion pure test behavior. If those
SLAM/base topics appear later, it leaves pure test mode automatically.

For a supervised motion test only, with clear floor, low speed, and a person at
the physical E-stop:

```bash
source /home/wte/wheeltec_robot/scripts/project_link_env.sh
source /home/wte/.config/project_link/voice_api.env
source /home/wte/wheeltec_robot/install/setup.bash
ros2 launch project_link_voice voice_direct_drive.launch.py enable_motion:=true
```

For a supervised fetch test after the robot has a verified safe manipulation
pose, start the visual grasp stack separately, then permit both the base direct
drive and the SO-101 grasp Action:

```bash
./scripts/start_visual_grasp_tmux.sh --restart
ros2 launch project_link_voice voice_direct_drive.launch.py \
  enable_motion:=true \
  enable_visual_grasp:=true
```

Supported local phrases include commands like `去厨房拿药瓶`. The voice node
passes ASR text to the LLM, which must call `navigate_to_location` or
`fetch_item_from_location`. Python then validates the named waypoint and maps
common spoken object names to YOLO-World targets, for example
`药瓶 -> medicine bottle` and `水杯 -> red cup`. The flow is:

```text
confirm fetch command
-> direct drive to the named map waypoint
-> stop at the safe grasp pose
-> /visual_grasp/connect_arm
-> /visual_grasp/set_torque true
-> /visual_grasp/track_and_grasp
```

`enable_visual_grasp:=false` is the default. In that mode the robot can stop at
the waypoint and announce that grasping is disabled, but it will not connect the
arm, enable torque, or call `TrackAndGrasp`.

The included local TTS bridge subscribes to `/voice/tts_text` and defaults to
Volcano bidirectional WebSocket TTS. `voice_dialog_node` also uses the same
Volcano adapter in-process for low-latency streamed LLM speech. If API values,
`pygame`, or `websockets` are missing, it falls back to console mock output.
Override deployed waypoint coordinates in a user-owned JSON file using the
`waypoints_override_file` parameter; do not modify packaged defaults on the
Orin.

### FunVAD hardware audio fixtures

The repository contains the capture contract at
`src/project_link_voice/test/audio/README.md` and an offline evaluator:

```bash
PYTHONPATH=src/project_link_voice python3 src/project_link_voice/tools/evaluate_vad.py path/to/capture.wav
```

Capture the prescribed quiet, fan, chassis-noise, distant-speech, and long-pause
clips on the real Orin USB microphone before enabling motion. No private or
uncurated audio recordings should be committed.

## 跌倒识别与飞书告警（第二摄像头）

`project_link_fall_response` 是独立的跌倒响应模块：它使用第二路摄像头
`/dev/FallCam` 拍一张 JPEG，调用硅基流动视觉模型按严格 JSON 判断是否疑似跌倒，
再通过 `/voice/tts_text` 播报固定提示。若语音项目在 15 秒内确认，则立即推送飞书
机器人告警；若取消则不推送；若 15 秒无响应，则自动推送到配置的飞书群。

```bash
source /home/wte/wheeltec_robot/scripts/project_link_env.sh
ros2 launch project_link_fall_response fall_response.launch.py camera_device:=/dev/FallCam
```

飞书 webhook 和签名密钥只从环境变量读取，不写入仓库配置。未配置完整时会安全失败，
不会推送。该模块不发布 `/cmd_vel`，不控制底盘或机械臂；
音频项目需要先完成唤醒词、声源定位和受控转向，再调用
`/fall_detection/assess_fall`。详细对接契约见
`docs/VOICE_FALL_DETECTION_INTEGRATION.md`。

## YOLO World 远程抓取（Orin 无 GUI）

`project_link_visual_grasp` 是独立的本地 YOLO-World + SO-101 视觉伺服栈：Orin
独占 USB 摄像头、模型推理和机械臂串口；Ubuntu 电脑只运行
`project_link_visual_grasp_gui`，通过 ROS 2 显示标注 JPEG、设置参数并发送控制。
它不使用豆包/VLM 云端识别，不启动 Nav2，不发布 `/cmd_vel`，也不改变当前 SLAM/TF
链路。

完成 Orin 依赖、模型和稳定设备名配置后，分别启动：

```bash
# Orin
cd /home/wte/wheeltec_robot
./scripts/start_visual_grasp_tmux.sh --restart

# Ubuntu GUI（与 Orin 使用相同 ROS_DOMAIN_ID=42）
ros2 run project_link_visual_grasp_gui visual_grasp_gui
```

使用 GUI 的自动发现列表选择 Orin。参数即时应用，并保存在 Orin 的
`~/.config/project_link/visual_grasp/`，不会改写仓库配置。接口、调度 action 与部署
检查见 `docs/VISUAL_GRASP_INTERFACE.md` 和 `docs/VISUAL_GRASP_ORIN_SETUP.md`。先确认
机械臂空间安全并保留物理急停，再连接、启用扭矩或开始抓取。
