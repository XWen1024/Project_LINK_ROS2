# Site Voice Mobile Manipulation Runbook

This is the fastest safe path for a site demo:

```text
make a good map
-> save named voice waypoints
-> dry-run voice tasks
-> enable direct drive
-> test visual grasp alone
-> enable voice fetch
```

## Emergency Plan B: Standalone LLM Voice Car Demo

If the site has no lidar/map/arm setup available, run only a bounded voice motion
demo with LLM Tool Calling and Volcano TTS:

```bash
cd /home/wte/wheeltec_robot
bash scripts/start_llm_voice_car_demo.sh --restart
```

Scan iFlytek serial, PyAudio devices, and API/TTS env first:

```bash
bash scripts/start_llm_voice_car_demo.sh --scan-only
```

If auto scan picks the wrong device:

```bash
bash scripts/start_llm_voice_car_demo.sh --restart --wakeup-port /dev/ttyUSB0 --audio-input-index 2
```

The LLM can only call one tool, `demo_motion`, with these actions:

```text
前进 / 走两步
后退 / 退两步
左转
右转
转个圈 / 转一圈
停止 / 取消
```

Text-only test:

```bash
ros2 topic pub --once /voice_demo/text_input std_msgs/msg/String "data: '请你往前走两步'"
ros2 topic pub --once /voice_demo/text_input std_msgs/msg/String "data: '帮我转个圈'"
ros2 topic pub --once /voice_demo/text_input std_msgs/msg/String "data: '停止'"
```

This publishes `/cmd_vel` directly for short bounded durations. Keep the robot
clear and keep a hand on the physical E-stop. Set the USB speaker as the OS
default output device before launch.

## Physical Setup

Use floor tape, but treat it as an operator convention, not a ROS origin.

- Put tape where the robot should start every time.
- Put the robot on the tape before mapping and before later demos.
- Start SLAM from the same heading if you want saved waypoint coordinates to stay useful.
- Do not move the robot by hand after SLAM starts unless you restart localization/SLAM.

The `map` frame is created by SLAM. The tape just helps you reproduce the same
initial real-world pose, so saved `map` waypoints line up again.

## Step 1: Map And Save

On Orin:

```bash
cd /home/wte/wheeltec_robot
bash scripts/site_map_and_save.sh --restart
```

Open RViz2 on the visualization computer:

```bash
export ROS_DOMAIN_ID=42
export ROS_LOCALHOST_ONLY=0
source /opt/ros/humble/setup.bash
rviz2
```

Set RViz Fixed Frame to `map`, then slowly drive or push only as your SLAM
procedure allows. When the map looks good, press Enter in the Orin script. It
saves:

```text
/home/wte/wheeltec_robot/maps/site_current.yaml
/home/wte/wheeltec_robot/maps/site_current.pgm
```

## Step 2: Save Voice Waypoints

The voice service reads this user-owned file:

```text
/home/wte/.ros/project_link_voice/waypoints.json
```

Option A, save where the robot is now:

```bash
cd /home/wte/wheeltec_robot
bash scripts/site_waypoints.sh save-current 客厅
bash scripts/site_waypoints.sh save-current 厨房
bash scripts/site_waypoints.sh list
```

Option B, save RViz clicked points:

```bash
cd /home/wte/wheeltec_robot
bash scripts/site_waypoints.sh save-click 客厅 厨房 取药点
```

Then in RViz use `Publish Point` and click those locations in the same order.
Clicked points save `x/y`; yaw defaults to the robot's current heading, or `0`
if TF is unavailable. For the direct-drive demo, keep points close and simple.

## Step 3: Voice Dry-Run

Create the private API file once:

```bash
mkdir -p /home/wte/.config/project_link
nano /home/wte/.config/project_link/voice_api.env
chmod 600 /home/wte/.config/project_link/voice_api.env
```

Expected values:

```bash
export SILICONFLOW_API_KEY=...
export VOLCANO_APP_ID=...
export VOLCANO_ACCESS_TOKEN=...
export VOLCANO_RESOURCE_ID=seed-tts-2.0
export VOLCANO_SPEAKER=...
```

Start dry-run voice:

```bash
cd /home/wte/wheeltec_robot
bash scripts/start_site_voice_stack.sh --restart
```

Dry-run accepts ASR/LLM/TTS and confirmation, but does not move. Test with text
before trying the iFlytek wake module:

```bash
ros2 topic pub --once /voice/text_input std_msgs/msg/String "data: '去客厅'"
ros2 topic pub --once /voice/text_input std_msgs/msg/String "data: '确认开始'"
```

## Step 4: iFlytek Wake And USB Speaker

Test wake serial alone first:

```bash
python3 scripts/serial_wakeup_dump.py --port /dev/ttyUSB0 --baud 115200
```

If the module appears as another port, check:

```bash
ls -l /dev/serial/by-id/
dmesg -w
```

For the USB speaker, first verify Linux sees it:

```bash
aplay -l
speaker-test -t wav -c 2
```

The Volcano TTS path plays through the default ALSA/Pulse device used by
`pygame`. If audio comes out of the wrong device, set the system default output
before launching the voice stack.

## Step 5: Enable Direct Drive

Only after `/map`, `/scan`, `/odom`, and `map -> base_footprint` are stable:

```bash
cd /home/wte/wheeltec_robot
bash scripts/start_site_voice_stack.sh --restart --enable-motion
```

Keep `enable_visual_grasp` off for the first driving test. Start with the nearest
saved waypoint and a person at the physical E-stop.

## Step 6: Test Visual Grasp Alone

Bring up visual grasp without voice first:

```bash
cd /home/wte/wheeltec_robot
./scripts/start_visual_grasp_tmux.sh --restart
```

On Ubuntu:

```bash
ros2 run project_link_visual_grasp_gui visual_grasp_gui
```

Use the GUI to verify camera, YOLO target, arm connection, torque, stop, and
manual/confirmed approach. Do not let voice call the arm until this is boringly
repeatable.

## Step 7: Full Voice Fetch

Only after direct drive and visual grasp both work independently:

```bash
cd /home/wte/wheeltec_robot
bash scripts/start_site_voice_stack.sh --restart --with-visual --enable-motion --enable-visual-grasp
```

Then say or publish:

```bash
ros2 topic pub --once /voice/text_input std_msgs/msg/String "data: '去厨房拿药瓶'"
ros2 topic pub --once /voice/text_input std_msgs/msg/String "data: '确认开始'"
```

Current behavior after a successful grasp: the robot stays at the grasp pose and
speaks completion. It does not return or place the object yet.

