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

## Current Status

- Date: 2026-08-10.
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
- The ESP32-C3 + VL53L0X USB bridge firmware and Windows GUI are bench-verified.
  A valid 43 mm sample with status 0 was captured. The repository now includes
  the headless `project_link_vl53l0x` sensor_msgs/Range serial node; Orin build
  and mounted-sensor field validation remain pending. The bench GUI and ROS node
  must never own the same USB serial port simultaneously.

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
docs/modules/navigation/C63A_BASE_AND_SLAM.md
docs/runbooks/SITE_VOICE_MOBILE_MANIPULATION.md
docs/modules/navigation/HANDOFF.md
docs/modules/uwb/HANDOFF.md
docs/modules/sensors/vl53l0x/HANDOFF.md
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
`docs/modules/uwb/HANDOFF.md`; do not enable it before measured
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
`docs/modules/navigation/HANDOFF.md`. The command-only quick reference is
`docs/modules/navigation/COMMANDS.md`.

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
  `docs/modules/navigation/C63A_BASE_AND_SLAM.md`.
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
`docs/runbooks/SITE_VOICE_MOBILE_MANIPULATION.md`. It covers one-command map
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

One wake event now starts a bounded continuous conversation. After every reply
finishes playing, the microphone automatically opens for the next turn; 8
seconds of follow-up silence returns to wake wait. `停止`, `取消`, `退出`, `退下`,
`休息`, `不用了`, `算了`, `再见`, and `拜拜` are handled locally, cancel active
robot work, speak the cached fixed reply `好的，我退下了`, and close the session.
Silence-only timeout closes the conversation but does not cancel a robot task
that is already running.

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
to `~/.ros/project_link_voice/voice_debug.jsonl`. The measured boundaries include
speech-end to FunVAD endpoint, VAD to ASR final, ASR to DeepSeek send, Tool Call,
Python execution, TTS first PCM, and first actual playback. Summarize recent
samples with:

```bash
python3 src/project_link_voice/tools/summarize_voice_timing.py --last 20
```

The path is:

```text
serial wake event -> 20 ms PCM capture
-> FunASR fsmn-vad endpointing + Volcano bidirectional streaming ASR in parallel
-> DeepSeek official LLM Tool Calling
-> Python safety validation and spoken confirmation
-> Nav2 /navigate_to_pose in production, or /voice/drive_to_point as fallback
-> optional /visual_grasp/track_and_grasp after arrival
-> Volcano bidirectional streaming TTS, first PCM played immediately
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
export PROJECT_LINK_ASR_PROVIDER=volcano
export VOLCANO_ASR_API_KEY=...
export VOLCANO_ASR_RESOURCE_ID=volc.seedasr.sauc.duration
export VOLCANO_ASR_ENDPOINT=wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async
export DEEPSEEK_API_KEY=...
export VOLCANO_APP_ID=...
export VOLCANO_ACCESS_TOKEN=...
export VOLCANO_RESOURCE_ID=seed-tts-2.0
export VOLCANO_SPEAKER=...
export QWEATHER_API_KEY=...   # optional; weather tool only
```

`PROJECT_LINK_ASR_PROVIDER=volcano` is the production default. To make an
explicit offline test with the retained local recognizer, set:

```bash
export PROJECT_LINK_ASR_PROVIDER=faster_whisper
export PROJECT_LINK_WHISPER_MODEL=/home/wte/.cache/project_link/models/faster-whisper-small
```

There is no automatic cloud-to-Whisper fallback and the Whisper model is not
prewarmed in Volcano mode. DeepSeek requests force
`thinking: {type: disabled}`. LLM text is streamed into the Volcano bidirectional
TTS session; the first PCM packet is queued immediately and later audio is
aggregated in 60 ms chunks. The pygame mixer uses a 512-sample buffer by default.
If no formal TTS first packet exists 500 ms after
FunVAD ends, the local cached `好的。` prompt is played only while the speaker is
idle. Fixed phrases use persistent metadata-checked MP3 cache; repeated dynamic
full-text replies use bounded TTL/LRU PCM cache.

The one-command launchers fail before starting ROS nodes if Volcano is selected
but no ASR credential is present. TTS `VOLCANO_APP_ID` / `VOLCANO_ACCESS_TOKEN`
must not be assumed to have ASR entitlement; use the dedicated
`VOLCANO_ASR_API_KEY` from the Speech API Key page unless a legacy ASR app/token
pair is known to be authorized.

Source it before launching voice:

```bash
source /home/wte/wheeltec_robot/scripts/project_link_env.sh
source /home/wte/.config/project_link/voice_api.env
source /home/wte/wheeltec_robot/scripts/project_link_voice_io.sh
source /home/wte/wheeltec_robot/install/setup.bash
```

Install the stable USB bindings once:

```bash
cd /home/wte/wheeltec_robot
bash scripts/install_project_link_voice_io_aliases.sh
```

The wake board is fixed at `/dev/project_link_wakeup` from USB serial `0004`.
The iFlytek microphone is selected by name and the C-Media USB speaker is bound
through its stable Pulse sink, so USB replug order no longer changes capture or
playback devices.

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
`docs/modules/sensors/fall-response/VOICE_INTEGRATION.md`。

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

# 加载末端 VL53L0X 影子监看或距离闭环时
./scripts/start_visual_grasp_tmux.sh --restart --with-tof

# Ubuntu GUI（与 Orin 使用相同 ROS_DOMAIN_ID=42）
ros2 run project_link_visual_grasp_gui visual_grasp_gui
```

使用 GUI 的自动发现列表选择 Orin。参数即时应用，并保存在 Orin 的
`~/.config/project_link/visual_grasp/`，不会改写仓库配置。接口、调度 action 与部署
检查见 `docs/modules/manipulation/INTERFACES.md` 和 `docs/modules/manipulation/ORIN_SETUP.md`。先确认
机械臂空间安全并保留物理急停，再连接、启用扭矩或开始抓取。

第一次装机或校准文件失配时，使用 GUI 的四步 LeRobot 校准面板，不要依赖 Orin
后台终端输入。完整现场流程见
`docs/modules/manipulation/CALIBRATION.md`。

末端测距由独立 `project_link_vl53l0x` 节点拥有串口并发布
`/visual_grasp/tof_range`。默认 `tof_enabled=false`；先启用影子模式验证距离和
`WOULD_GRASP` 决策，再经现场标定后启用 `tof_control_enabled=true`。控制模式下，
ToF 数据过期或不足会让机械臂保持停止，不会退回目标框面积自动夹取。
抓取入口还要求显式设置 `tof_calibrated=true`，避免把仓库中的占位距离当成实测阈值。

### Windows 一体化测试台

现场上 Orin 前，或需要完全在 Windows 上运行时，可直接测试摄像头、YOLO-World、
SO-101 六关节、夹爪、三组预设姿态、卸力示教、VL53L0X、影子判断和完整自动抓取。
Windows GUI 还提供非交互式 LeRobot 中位与全行程校准，不需要在终端按 Enter。该工具
不依赖 ROS 2，默认不连接机械臂、不启用扭矩、不启用 ToF 控制：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_windows_visual_grasp_lab.ps1
```

启动脚本会自动寻找原 `VisualTracker` 虚拟环境并加载其本地 LeRobot 源码。运行前必须
关闭旧 VisualTracker、VL53L0X Monitor、Orin 视觉抓取节点和其他占用相同 COM 口或
摄像头的程序。GUI 的刷新串口和机械臂扭矩控制使用上下独立布局，小窗口下也不会占用
同一位置。完整初始化、校准、参数和第一次抓取流程见
`docs/modules/manipulation/WINDOWS_LAB.md`；快速入口见
`tools/windows_visual_grasp_lab/README.md`。

如果脚本提示旧 VisualTracker venv 无法启动，它会自动跳过该环境。当前电脑上的旧 venv
可能仍指向已卸载的 Python 3.12；按 Windows 完整教程在仓库创建新的 `.venv`，安装依赖
后再运行 `-CheckOnly` 和 `-SmokeTest`。

Windows 机械臂连接后会立即再次关闭 LeRobot `configure()` 临时启用的扭矩。若单个
Feetech 电机因过热拒绝普通写入，程序会逐电机重试、同步广播断扭矩并读取确认；断开时
无论断扭矩结果如何都会继续释放 COM 口。任何 `Overheat error` 都必须物理断电冷却，
禁止继续校准或重新启用扭矩。

Windows GUI 的连接、校准、关节读取和运动命令共享同一个可重入总线锁。连接或校准
工作线程运行时，500 ms 状态刷新不会再并发读取关节；若 Feetech SDK 因此前异常遗留
内部 `Port is in use` 标志，断扭矩路径会在独占总线后清除该标志并重试。

LeRobot 校准文件使用固定 ID `so101_slave`。连接时若文件已经加载但电机 EEPROM 与
文件不一致，Windows 控制台会在扭矩关闭状态下自动重写已保存校准并读回验证；只有
自动恢复仍失败时才进入 `REQUIRED`，同时报告每个关节的 offset/min/max 差异。

Windows 校准采样会把 STS3215 的 16 位 sign-magnitude 负位置解码为有符号值，再根据
实测完整行程调整 homing，使最终 min/max 落入硬件合法的 `0..4095`。旧文件若包含
`327xx/329xx` 范围值会被判为不可恢复，必须用新版重新校准一次，禁止继续把它写入
12 位位置限位寄存器。

Windows 预设姿态权限已按扭矩状态拆分：扭矩关闭时录制，扭矩开启时前往。目标跟踪是
纯视觉状态，不会自动移动机械臂；自动抓取固定执行
`打开夹爪 → pregrasp → CENTERING → APPROACHING → GRASPED`。`standby` 用于任务前后
收拢，`placement` 在抓取成功后由操作员或任务中枢明确调用。

预设位执行改为五关节同步平滑插值，不再使用“按当前反馈追一步、最后才转底座”的分段
算法。视觉居中修正受单周期步长限制，逼近受 `joint_command_limit` 和
`grasp_timeout_sec` 保护；没有满足检测框或 ToF 夹取条件时会在软限位前停止。重新校准
会清除旧预设，要求现场重新录制。

Windows 控制器状态变化现在会写入“参数与日志”页。预设轨迹到达超时边界时先读取反馈，
只要关节已经处于 `arrive_threshold` 内就按到位处理；真正未到位的超时会列出各关节剩余
误差。自动抓取在移动前还会检查 `pregrasp` 是否超出 `joint_command_limit`，避免到位后
刚进入视觉伺服就立即显示 `ERROR`。

完整五关节轨迹命令不再在每次串口写入前执行一次冗余反馈读取。此前这次读取若在轨迹
末尾偶发失败，会出现机械臂已经到位、控制器却立即进入 `ERROR` 的假故障。现在写命令和
到位反馈确认相互独立，短暂反馈失败只会等待下一次确认。

预设位还受 `preset_joint_limit` 保护，默认要求五个关节处于归一化 `+/-95` 以内。接近
`+/-100` 的值代表校准行程端点，机械臂在负载、摩擦或机械限位影响下通常无法稳定达到；
录制和执行都会拒绝这种预设，而不是等待超时或把较大的实际误差伪装成到位。

待机位是例外：它是无抓取负载、由操作员监督执行的收拢/初始姿态，可以使用独立的
`standby_joint_limit=99.5`。因此待机位可保存到比抓取位和放置位更靠近校准端点的位置；
`pregrasp` 和 `placement` 仍保持 `preset_joint_limit=95`，避免操作姿态顶住机械限位。

预设到位默认对普通关节使用 `arrive_threshold=2.0`，对承受较大重力和齿隙影响的肘关节
单独使用 `elbow_arrive_threshold=5.0`。Windows 参数页显示这两个值；不要为了肘关节
残差而把所有关节的统一容差一起放宽。

对 `2.03` 对 `2.00` 这类硬阈值边缘误差，控制器还会在
`arrive_stable_margin=0.75` 范围内检查反馈是否连续稳定，而不是直接等到超时报错。
Windows 测试台默认生成
`%APPDATA%\ProjectLINK\visual_grasp_lab\logs\visual_grasp_debug_*.jsonl`，记录逐周期目标、
反馈、校准数据和错误瞬间的 Feetech 诊断寄存器，现场复现后可直接提交该文件排查。

视觉居中如果单帧修正略微超过 `joint_command_limit`，现在会把该关节安全钳位在软限位
并继续观察目标响应，不再第一帧直接进入 `ERROR`。只有连续
`centering_limit_hold_cycles=3` 个新 YOLO 结果仍顶住软限位且无法居中，才会停止并要求重新录制
离限位更远的待抓取位或调整画面中心偏移。

Windows 视频画面会以黄色十字显示 `center_offset_x/y` 对应的视觉伺服期望中心，以绿色
圆点显示检测框中心，并用连线显示误差方向。更换摄像头位置或重新校准机械臂后，应先将
偏移归零观察，再小步调整，不能直接沿用旧相机安装产生的较大偏移值。

Windows 视频下方提供“点击画面设置视觉抓取中心”。开启后在实际视频内容上点击一次，
程序会正确处理画面缩放和黑边，将点击坐标换算为原始摄像头像素，立即更新并保存
`center_offset_x/y`。该点是视觉伺服希望检测框中心对齐的位置，不是三维机械臂位姿。
界面同时提供“使用当前绿色圆点”，可直接把当前检测框中心设为对齐位置。不要把按钮理解
为选择瓶身的夹爪接触点。当前 `centering_tilt_motion_enabled=false` 时，点击点的 X 坐标用于
真实水平居中，Y 坐标作为画面和试教轨迹参考；需要改变实际抓取高度时应微调并重新录制
pregrasp，而不是让 shoulder lift 在居中阶段追逐画面 Y 误差。每次手动点击都会自动关闭
`auto_lock_vertical_center_on_pregrasp`，因此自动抓取不会再覆盖刚保存的点位。

实际抓取高度另由 `approach_profile_wrist_trim` 微调。它只改变最终 wrist flex 数值，不改变
肩关节总行程、ToF 阈值或其它关节轨迹，并在核心中强制限制为 `-10..+10`。现场发现夹爪
偏低时先试 `+2`；如果实际方向相反，立即恢复 `0` 后再试 `-2`，每次只改 2 个单位。

为抑制 YOLO 框偶发抽动，跟踪器会优先关联上一稳定框，拒绝中心瞬移或面积突变的候选，
并短暂保持上一稳定框。每个新 YOLO 推理结果最多只允许下发一次视觉伺服命令，旧框不会
被 GUI 定时器重复使用。视觉居中使用最近 3 个新结果的中值，至少观察 2 个新结果，
大偏差最多修正 `1.5`，进入 `centering_slow_zone=0.12` 后自动减速，并连续 2 次确认居中
后才进入逼近。当前相机安装的纵向方向为 `tilt_direction=-1`；若方向设反，框会越调越远。
若受重力或静摩擦影响，单个 `1.5` 目标不足以让肩关节启动，控制器会逐个新检测结果累计
命令目标，但相对实际反馈最多领先 `centering_max_command_lead=4.0`，避免永远重复同一个
小目标，也避免无边界地把命令推向关节端点。
逼近阶段使用相同的有界累计策略，默认 `approach_max_command_lead=4.0`；到达软限位、
抓取超时或 ToF 数据失效时仍然立即停止或保持，不会因为累计目标绕过安全保护。
被拒绝的跳变帧会以橙色 `HELD` 框显示；这些帧只用于提示，视觉伺服完全停止发命令，
直到重新得到可信检测框。

Windows “参数与日志”页的参数区域可独立上下滚动，数值框已禁用鼠标滚轮修改，避免滚动
页面时误改参数。顶部提供“撤销修改（恢复已保存）”和“一键恢复推荐参数”；推荐参数复位
会保留摄像头、串口、模型路径和已经选好的画面对齐位置。

Windows 卸力示教现在保持 YOLO 目标跟踪，并将每个样本平铺记录到
`%APPDATA%\ProjectLINK\visual_grasp_lab\demos\visual_demo_*.csv`。字段包括六关节、bbox
坐标/中心/面积比例、目标中心误差、检测可信状态与序号、画面尺寸和 ToF。该数据用于现场
人工示教正确的“居中后水平逼近”视角。当前已使用 `visual_demo_20260810_190054.csv`
提取相对轨迹：肩关节约 `+34`、肘关节约 `+12.3`、腕关节约 `-54`。

旧版逼近中的固定肘关节多项式已经删除。现场日志证明，当 shoulder lift 约为 `-80` 时，
旧公式会直接生成约 `+83` 的 elbow flex 目标，造成末端向地面快速下压。新版所有逼近命令
从真实关节反馈开始插值，但 shoulder lift 终点固定为“保存的 pregrasp +34”，避免预设位以
`stable_near` 少到位约 2–3 个单位后又把整条抓取轨迹截短。系统通过
`visual_servo_max_joint_step=6` 在发送前拒绝任何单次大跳变。普通“停止运动”会立即读取并
保持当前五关节位置，避免电机继续追逐上一条目标；红色紧急停止仍优先直接关闭扭矩。

第一版近距离抓取采用显式视觉交接。`CENTERING` 默认设置
`centering_tilt_motion_enabled=false`，只允许肩部水平旋转，不再通过 shoulder lift 伸臂
修正纵向误差。进入 `APPROACHING` 后，当 bbox 高度达到画面 `85%`、面积达到 `18%`，或
ToF 小于 `0.19 m`，并且 ToF 不超过 `0.21 m` 的近场门时，状态切换为
`FINAL_APPROACH`。该阶段允许 YOLO 因遮挡或画面裁切而消失，只使用有效 ToF 和有界的
试教 shoulder-lift/elbow/wrist 水平轨迹；ToF 无效时保持，超过 `6 s`、盲走肩关节 `20`
个归一化单位或到达保存的 pregrasp `+34` 终点时失败关闭。按当前现场要求，最终闭合阈值为
`final_grasp_tof_m=0.090`。终点命令下发后仅等待
`final_approach_endpoint_settle_sec=0.75` 让关节反馈和 ToF 更新；该调整不会自动放宽超时、
盲走或关节行程上限。
Windows 默认关闭 `auto_lock_vertical_center_on_pregrasp`，自动抓取使用并保留用户点击保存
的 `center_offset_x/y`。只有明确开启自动锁定时，第一帧可信绿色框才会建立临时纵向参考，
并通过 `auto_lock_vertical_center_offset_ratio=0.10` 将黄色目标点向下移动框高 `10%`。
关闭纵向关节修正时，进入逼近只要求水平居中。重复显示的同一 YOLO 结果不再覆盖控制器
消息，因此界面不会在“纵向
未对齐”和“等待新帧”之间闪烁。视觉交接开启时，自动抓取启动前强制要求
`tof_enabled`、`tof_control_enabled`、`tof_calibrated` 全部开启。
---

## Qwen3.5 Omni Realtime 独立语音链路

`project_link_qwen_realtime_voice` 是与现有 `project_link_voice` 完全独立启动的
实时语音方案。它在同一条 DashScope WebSocket 中完成语义 VAD、实时 ASR、
Function Calling 和 24 kHz PCM 语音输出；原有火山 ASR、DeepSeek、火山 TTS
链路保持不变。两套语音节点禁止同时启动。

```bash
cd /home/wte/wheeltec_robot
source /opt/ros/humble/setup.bash
source scripts/project_link_env.sh
source /home/wte/.config/project_link/qwen_realtime.env
colcon build --packages-select project_link_qwen_realtime_voice
source install/setup.bash
bash scripts/start_qwen_realtime_voice.sh pure-test
```

可用模式为 `pure-test`、`demo`、`nav2-dry`、`nav2` 和 `fetch`。生产运动仍只
允许命名航点并要求本地明确“确认开始”；Qwen 不直接持有 ROS Action 或
`/cmd_vel`。完整参数和 AEC 要求见
`src/project_link_qwen_realtime_voice/README.md`，协议调研见
`docs/modules/voice/qwen-realtime/ORIN_GUIDE.md`。

启动脚本会先加载 ROS、虚拟环境和工作区 setup，再开启 Bash `nounset`；不要把
`set -u` 提前到 `/opt/ros/humble/setup.bash` 之前。
脚本还会把 `.venv-qwen-realtime` 的 site-packages 显式加入 `PYTHONPATH`，以兼容
Humble `ament_python` 入口保留 `/usr/bin/python3` shebang 的情况。

启动后日志必须显示 `Audio input ready`，唤醒并开始监听后必须显示
`Microphone PCM upload started`。设备按 `XFM-DP-V0.0.18` 名称匹配，不依赖重新插拔后变化的卡号或 PyAudio 索引。
服务端只为 VAD 提交的音频自动创建响应；文本输入和 Function Calling 结果都必须显式发送 `response.create`。
首轮无语音超时从缓存唤醒播报播放完成、麦克风真正开始上传后计时，不包含“我在，请说”的播放时间。
首轮成功识别后默认保持多轮连续对话，跟随静音 30 秒才自动退下；说“停止”“取消”“退出”“退下”或“休息”等明确口语命令会立即结束并回到唤醒等待。

天气工具需要同时配置和风控制台中的 Key 与项目专属 API Host：

```bash
export QWEATHER_API_KEY=...
export QWEATHER_API_HOST=YOUR_PROJECT_HOST.re.qweatherapi.com
```
