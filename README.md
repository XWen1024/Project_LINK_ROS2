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

The current engineering phase is narrower: **SLAM-first validation with a direct
RViz A-to-B motion check**. The previous `rf2o + EKF + slam_toolbox` route now
works in RViz2 and C63A base odom is integrated. The next requested minimum loop
is not Nav2: keep SLAM/TF running, click A and B in RViz, and send low-speed
`/cmd_vel` directly toward B with no planning or obstacle avoidance.

## Current Status

- Date: 2026-07-11.
- Main onboard computer: Jetson Orin Nano.
- Orin workspace: `/home/wte/wheeltec_robot`.
- Old Orin workspace backup: `/home/wte/wheeltec_robot_backup_20260627_1250`.
- Remote repository: `git@github.com:XWen1024/Project_LINK_ROS2.git`.
- The migrated workspace builds successfully with `colcon build --symlink-install`.
- C63A base serial link is confirmed on `/dev/wheeltec_controller` at `115200`;
  `/odom`, `/imu/data_raw`, and `/PowerVoltage` return at about `20 Hz` after the
  C63A board is healthy.
- Known-good SLAM fallback: `rf2o + EKF + slam_toolbox`.
- Current priority: Point-LIO Phase A coordinate validation. The known-good
  rf2o/EKF/SLAM route remains the fallback; Point-LIO will not be used for map
  building or direct A-to-B motion until its planar base TF passes hardware
  validation.

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
```

Important ROS 2 packages:

```text
turn_on_wheeltec_robot        Base serial, robot description, lidar, EKF, SLAM launch
wheeltec_nav2                 Nav2 launch/config package; not used for direct A/B
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

The initial Orin Phase A bringup was verified on 2026-07-11: cloud is about
`9.8 Hz`, IMU about `249 Hz`, and raw/projected odom plus registered cloud are
at lidar cadence. The projected TF correctly has zero z/roll/pitch. Its initial
yaw is about `-65 degrees`, so perform a low-speed straight-line calibration and
adjust only `odom_to_lio_odom_yaw` before any Phase B map test.

Phase B, Point-LIO odometry plus `slam_toolbox` 2D map, is blocked until the
Phase A checks above pass:

```bash
cd /home/wte/wheeltec_robot
./start_point_lio_tmux.sh --restart --with-2d-map
```

The initial Phase B bringup was verified on 2026-07-11 without Nav2 or motion
commands: `/scan` runs at about `9.6 Hz`, `/map` publishes an occupancy grid,
and `map -> odom -> base_footprint` is continuous. The initial yaw alignment is
`odom_to_lio_odom_yaw: 1.135`; refine it only after a real straight-line chassis
check.

The tmux session is `project_link_point_lio` and contains:

- `lidar`: Unitree L1 / UniLidar driver
- `robot`: robot description, plus `/scan` conversion when `--with-2d-map` is used
- `lio`: `point_lio_unilidar_l1.launch.py`
- `check`: live monitor for `/unilidar/cloud`, `/unilidar/imu`,
  `/odom_lio_raw`, `/odom_lio`, `/point_lio/cloud_registered`, `/scan`, `/map`,
  and the raw/projected TF chain

Manual Point-LIO launch:

```bash
cd /home/wte/wheeltec_robot
source scripts/project_link_env.sh
ros2 launch turn_on_wheeltec_robot point_lio_unilidar_l1.launch.py
```

In Phase A, this launch publishes the static TF `unilidar_link -> unilidar_lidar`
because `unilidar_p2s.launch.py` is not running. In Phase B, the tmux script sets
`publish_lidar_static_tf:=false` because `unilidar_p2s.launch.py` already owns the
same static TF.

The tmux script waits for real `/unilidar/cloud` and `/unilidar/imu` messages
before starting Point-LIO. This avoids judging the stack while the lidar driver is
visible in the ROS graph but has not produced data yet.

To tune point-cloud direction, override the static TF values when starting tmux:

```bash
LIDAR_TF_YAW=0.0 ./start_point_lio_tmux.sh --restart --with-2d-map
LIDAR_TF_YAW=3.14159 ./start_point_lio_tmux.sh --restart --with-2d-map
```

Use `LIDAR_TF_ROLL`, `LIDAR_TF_PITCH`, and `LIDAR_TF_YAW` for orientation
corrections. Use `LIDAR_TF_X`, `LIDAR_TF_Y`, and `LIDAR_TF_Z` only for small frame
offset corrections between `unilidar_link` and the driver frame `unilidar_lidar`.

Do not run `rf2o_slam_toolbox.launch.py` at the same time as the Point-LIO launch.
Only one node stack should publish `odom -> base_footprint`. In the Point-LIO
stack, the raw node owns `lio_odom -> lio_base` and `lio_planar_projection`
owns the projected base TF.

## Hardware Test Strategy

- Lidar-only check: connect only lidar and validate `/scan` plus RViz2 display.
- Lidar SLAM check: connect lidar, publish robot description TF, then run the
  current SLAM launch. This can test whether laser odometry is viable.
- Full current SLAM check: connect lidar plus STM32 base controller so `/odom` is
  available. Do not start Nav2 and do not publish `/cmd_vel`.
- C63A base handoff and keyboard teleop details are in
  `docs/C63A_BASE_AND_SLAM_HANDOFF.md`.
- Differential keyboard teleop helper:
  `scripts/ssh_c63_keyboard_teleop.ps1` starts `/tmp/c63_keyboard_teleop.sh` on
  Orin over SSH. It publishes `/cmd_vel`, uses dead-man behavior, and exits with
  a stop command.
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

The path is:

```text
serial wake event -> FunASR fsmn-vad endpointing -> faster-whisper
-> local named-waypoint match -> spoken confirmation -> /voice/drive_to_point Action
-> guarded low-speed /cmd_vel
```

Safety rules:

- `enable_motion:=false` is the default. In this mode confirmations are dry-run
  only and the drive Action server rejects every goal.
- Voice commands only accept saved `map` waypoints. `确认前往` is mandatory after
  the target is repeated; `停止` or `取消` cancels the active Action and emits zero
  velocity commands.
- `ab_drive_server` stops on cancel, TF loss, action timeout, watchdog expiry,
  process shutdown, or goal completion. A physical E-stop is still mandatory.
- Do not launch it alongside `scripts/rviz_ab_drive.py --enable-motion`, because
  both can publish `/cmd_vel`.

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
parses the named waypoint and maps common spoken object names to YOLO-World
targets, for example `药瓶 -> medicine bottle` and `水杯 -> red cup`. The flow is:

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
`espeak-ng -v zh`. Install/configure an approved Chinese TTS command before
hardware tests. The optional SiliconFlow chat path reads `SILICONFLOW_API_KEY`
from the environment and can only produce text replies; it is never a motion
control path. Override deployed waypoint coordinates in a user-owned JSON file
using the `waypoints_override_file` parameter; do not modify packaged defaults
on the Orin.

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
