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

The current engineering phase is narrower: **SLAM-first validation**. The previous
`rf2o + EKF + slam_toolbox` route now works in RViz2, so the active subphase is
Point-LIO 3D lidar odometry. Nav2 builds and its obvious launch issue has been
fixed, but Nav2 is intentionally paused until SLAM, odom, and the TF tree are
stable.

## Current Status

- Date: 2026-06-27.
- Main onboard computer: Jetson Orin Nano.
- Orin workspace: `/home/wte/wheeltec_robot`.
- Old Orin workspace backup: `/home/wte/wheeltec_robot_backup_20260627_1250`.
- Remote repository: `git@github.com:XWen1024/Project_LINK_ROS2.git`.
- The migrated workspace builds successfully with `colcon build --symlink-install`.
- Known-good SLAM fallback: `rf2o + EKF + slam_toolbox`.
- Current priority: validate Point-LIO with `/unilidar/cloud` and `/unilidar/imu`,
  then connect it back to `slam_toolbox` for a 2D `/map`.

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

Important ROS 2 packages:

```text
turn_on_wheeltec_robot        Base serial, robot description, lidar, EKF, SLAM launch
wheeltec_nav2                 Nav2 launch/config package; not current test target
wheeltec_slam_toolbox         slam_toolbox configs and wrappers
rf2o_laser_odometry           Laser-scan odometry
serial                        Vendored serial library
wheeltec_robot_msg            Custom Wheeltec messages
```

## Build On Orin

```bash
cd /home/wte/wheeltec_robot
source /opt/ros/humble/setup.bash
colcon build --symlink-install
source install/setup.bash
```

Expected packages:

```bash
ros2 pkg list | grep -E 'turn_on_wheeltec_robot|wheeltec_nav2|wheeltec_slam_toolbox|rf2o_laser_odometry|wheeltec_robot_msg|serial'
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

- `lidar`: Unitree L1 / UniLidar driver on `/dev/unilidar`
- `robot`: split panes for `unilidar_p2s.launch.py` and
  `robot_mode_description.launch.py`
- `slam`: current `rf2o_slam_toolbox.launch.py`
- `check`: live topic/TF monitor for `/scan`, `/odom_rf2o`,
  `/odometry/filtered`, `/map`, and `map -> odom`

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

1. Start the robot description TF:

   ```bash
   ros2 launch turn_on_wheeltec_robot robot_mode_description.launch.py
   ```

2. Start lidar and confirm `/scan`.

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

3. Start the current SLAM candidate:

   ```bash
   ros2 launch turn_on_wheeltec_robot rf2o_slam_toolbox.launch.py
   ```

4. Verify topics and TF:

   ```bash
   ros2 topic hz /odom_rf2o
   ros2 topic hz /odometry/filtered
   ros2 topic hz /map
   ros2 run tf2_ros tf2_echo odom base_footprint
   ros2 run tf2_ros tf2_echo map odom
   ```

Current intended flow:

```text
/scan
-> rf2o_laser_odometry
-> /odom_rf2o
-> robot_localization EKF
-> /odometry/filtered and odom -> base_footprint TF
-> slam_toolbox
-> /map and map -> odom TF
```

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

Phase B, Point-LIO odometry plus `slam_toolbox` 2D map:

```bash
cd /home/wte/wheeltec_robot
./start_point_lio_tmux.sh --restart --with-2d-map
```

The tmux session is `project_link_point_lio` and contains:

- `lidar`: Unitree L1 / UniLidar driver
- `robot`: robot description, plus `/scan` conversion when `--with-2d-map` is used
- `lio`: `point_lio_unilidar_l1.launch.py`
- `check`: live monitor for `/unilidar/cloud`, `/unilidar/imu`, `/odom_lio`,
  `/point_lio/cloud_registered`, `/scan`, `/map`, and TF

Manual Point-LIO launch:

```bash
cd /home/wte/wheeltec_robot
source scripts/project_link_env.sh
ros2 launch turn_on_wheeltec_robot point_lio_unilidar_l1.launch.py
```

Do not run `rf2o_slam_toolbox.launch.py` at the same time as the Point-LIO launch.
Only one node stack should publish `odom -> base_footprint`.

## Hardware Test Strategy

- Lidar-only check: connect only lidar and validate `/scan` plus RViz2 display.
- Lidar SLAM check: connect lidar, publish robot description TF, then run the
  current SLAM launch. This can test whether laser odometry is viable.
- Full current SLAM check: connect lidar plus STM32 base controller so `/odom` is
  available. Do not start Nav2 and do not publish `/cmd_vel`.
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
