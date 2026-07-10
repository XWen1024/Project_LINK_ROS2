# Project LINK C63A Base And SLAM Handoff

This handoff is for bringing the real robot base into the current ROS 2 project,
then using it together with the existing SLAM-first stack. The current target is
not Nav2. First prove base odom, lidar, TF, and mapping are stable.

## Current Hardware Facts

- Onboard computer: Jetson Orin Nano.
- Orin workspace: `/home/wte/wheeltec_robot`.
- ROS 2 distro: Humble.
- Base controller: Wheeltec C63A / STM32.
- Chassis mode in this project: differential base, `mini_diff`.
- C63A ROS port:
  - USB VID/PID: `1a86:55d4`.
  - Expected udev alias: `/dev/wheeltec_controller`.
  - Observed device: `/dev/ttyACM0`.
  - Baud rate: `115200`.
- C63A return data has been confirmed after power cycling the board:
  - `/odom` publishes near `20 Hz`.
  - `/imu/data_raw` publishes near `20 Hz`.
  - `/PowerVoltage` was about `26.27 V`.
  - Raw serial data contains C63A frames with `0x7b ... 0x7d`.

## Important Safety Rule

Only publish `/cmd_vel` when the robot is safe:

- wheels lifted, or
- motor power disconnected, or
- a person is physically present at the E-stop.

Do not start Nav2 until SLAM, odom, and TF are stable.

## Base Bringup

Start only the C63A base serial node:

```bash
cd /home/wte/wheeltec_robot
source install/setup.bash
export ROS_DOMAIN_ID=42
export ROS_LOCALHOST_ONLY=0
ros2 launch turn_on_wheeltec_robot base_serial.launch.py
```

Expected log:

```text
wheeltec_robot Data ready
wheeltec_robot serial port opened
```

In another terminal, verify return data:

```bash
source /home/wte/wheeltec_robot/install/setup.bash
export ROS_DOMAIN_ID=42
export ROS_LOCALHOST_ONLY=0
ros2 topic hz /odom
ros2 topic hz /imu/data_raw
ros2 topic echo --once /PowerVoltage
```

Expected:

- `/odom`: around `20 Hz`.
- `/imu/data_raw`: around `20 Hz`.
- `/PowerVoltage`: plausible battery voltage.

If topics exist but no messages arrive, check raw serial:

```bash
stty -F /dev/wheeltec_controller 115200 raw -echo -echoe -echok
timeout 8s dd if=/dev/wheeltec_controller bs=1 count=128 | od -An -tx1 -v
```

If this returns 0 bytes, the Orin can open the USB device but C63A is not sending
status frames. Power cycle C63A and re-check board mode, E-stop, and base power.

## Keyboard Base Test

The repo provides a differential-drive SSH keyboard teleop helper.

From Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File "C:\Users\XWen1024\Documents\ROS2小车\scripts\ssh_c63_keyboard_teleop.ps1"
```

Or directly on Orin:

```bash
/tmp/c63_keyboard_teleop.sh
```

Keys:

```text
w/s       forward/backward
a/d       rotate left/right
space/x   immediate stop
r/f       increase/decrease linear speed
t/g       increase/decrease angular speed
h         help
Ctrl-C    stop and exit
```

Current teleop behavior:

- Differential base only. No lateral movement keys.
- Dead-man behavior: if movement key repeats stop, velocity expires quickly.
- The status line shows current command and speed percentage.
- Defaults are intentionally gentle:
  - linear speed starts at `0.04 m/s`.
  - angular speed starts at `0.20 rad/s`.
- Current limits:
  - max linear speed: `0.60 m/s`.
  - max angular speed: `1.50 rad/s`.

For a one-shot test instead of interactive teleop:

```bash
bash /tmp/c63_cmd_vel_once.sh forward --speed 0.04 --duration 0.8
bash /tmp/c63_cmd_vel_once.sh stop
```

## Current SLAM Routes

Do not run the fallback rf2o route and Point-LIO route at the same time. Only one
stack should publish `odom -> base_footprint`.

### Current Integrated Route

The C63A base is now part of the default known-good rf2o SLAM tmux bringup:

```bash
cd /home/wte/wheeltec_robot
./start_slam_tmux.sh --restart
```

This starts:

- `base`: `base_serial.launch.py`, publishing `/odom`, `/imu/data_raw`, and
  `/PowerVoltage`.
- `lidar`: Unitree L1 / UniLidar driver.
- `robot`: pointcloud-to-laserscan plus robot description.
- `slam`: waits for `/odom` and `/scan`, then starts
  `rf2o_slam_toolbox.launch.py`.
- `check`: live topic and TF monitor.

For lidar-only debugging without the C63A base:

```bash
./start_slam_tmux.sh --restart --no-base
```

### Known-Good Fallback

Use the rf2o + EKF + slam_toolbox stack when a conservative fallback is needed:

```bash
cd /home/wte/wheeltec_robot
./start_slam_tmux.sh --restart
```

Data flow:

```text
/scan
-> rf2o_laser_odometry
-> /odom_rf2o
-> robot_localization EKF
-> /odometry/filtered and odom -> base_footprint TF
-> slam_toolbox
-> /map and map -> odom TF
```

This route also uses the C63A `/odom` as an EKF input in
`rf2o_slam_toolbox.launch.py`.

### Current Candidate: Point-LIO

Phase A, odometry only:

```bash
cd /home/wte/wheeltec_robot
./start_point_lio_tmux.sh --restart
```

Expected data flow:

```text
/unilidar/cloud + /unilidar/imu
-> point_lio
-> /odom_lio and odom -> base_footprint TF
```

Phase B, Point-LIO plus 2D map:

```bash
cd /home/wte/wheeltec_robot
./start_point_lio_tmux.sh --restart --with-2d-map
```

Expected added flow:

```text
/scan + Point-LIO odom TF
-> slam_toolbox
-> /map and map -> odom TF
```

## Bringup Order For The Real Robot

1. Power on C63A and verify `/dev/wheeltec_controller`.
2. Run `base_serial.launch.py` alone.
3. Verify `/odom`, `/imu/data_raw`, and `/PowerVoltage`.
4. With wheels lifted, test keyboard teleop and confirm odom changes.
5. Stop teleop and leave no extra `/cmd_vel` publishers running.
6. Start lidar and verify `/unilidar/cloud`, `/unilidar/imu`, and `/scan`.
7. Run Point-LIO Phase A and verify `/odom_lio` plus `odom -> base_footprint`.
8. If stable, run Point-LIO Phase B and verify `/map` plus `map -> odom`.
9. Only after SLAM and TF are stable, return to Nav2.

## Quick Health Checks

Device and alias:

```bash
lsusb | grep -Ei '1a86|55d4|C63|QinHeng'
ls -l /dev/wheeltec_controller /dev/ttyACM0
udevadm info -q property -n /dev/wheeltec_controller | grep -E 'ID_VENDOR_ID|ID_MODEL_ID|ID_SERIAL|ID_MODEL|DEVNAME'
```

ROS process check:

```bash
ps -eo pid,cmd | grep -E 'wheeltec_robot_node|base_serial|cmd_vel' | grep -v grep
```

Topic and TF check:

```bash
ros2 topic hz /odom
ros2 topic hz /imu/data_raw
ros2 topic hz /scan
ros2 run tf2_ros tf2_echo odom base_footprint
ros2 run tf2_ros tf2_echo map odom
```

## Known Failure Pattern

C63A may enumerate over USB while the firmware side is not sending status data.
In that state:

- `/dev/wheeltec_controller` exists.
- `wheeltec_robot_node` logs `serial port opened`.
- ROS topics exist.
- `/odom`, `/imu/data_raw`, and `/PowerVoltage` do not publish messages.
- Raw serial read returns 0 bytes.

When this happens, power cycle C63A and check E-stop, board mode, handheld
controller state, and base power. After recovery, return data should resume near
20 Hz.
