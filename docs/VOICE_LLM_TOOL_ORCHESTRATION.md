# Voice LLM Tool Orchestration Runbook

This is the production voice path for the current no-Nav2 direct-drive phase.

```text
wakeup -> FunVAD -> faster-whisper -> SiliconFlow Tool Calling
-> Python safety executor -> Volcano TTS confirmation
-> DriveToPoint -> optional TrackAndGrasp
```

## API Environment

Secrets stay outside Git:

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
export QWEATHER_API_KEY=...
```

Before launching:

```bash
source /home/wte/wheeltec_robot/scripts/project_link_env.sh
source /home/wte/.config/project_link/voice_api.env
source /home/wte/wheeltec_robot/install/setup.bash
```

## Tool Boundary

The LLM can call only whitelisted tools:

```text
get_weather
get_current_location
save_waypoint
list_saved_locations
navigate_to_location
fetch_item_from_location
cancel_current_task
```

Python owns all safety-critical effects:

```text
LLM tool call
-> validate tool name and arguments
-> validate named waypoint and TF/SLAM readiness
-> speak fixed safety summary
-> wait for explicit 确认开始
-> call ROS 2 action/service
```

The LLM must never publish `/cmd_vel`, enable torque, call ROS actions directly,
or invent free-form coordinates.

## Launch

Pure voice test on Windows/local machine:

```bash
ros2 launch project_link_voice voice_pure_test.launch.py
```

Dry-run on Orin after build:

```bash
ros2 launch project_link_voice voice_direct_drive.launch.py
```

Supervised direct-drive test only:

```bash
ros2 launch project_link_voice voice_direct_drive.launch.py enable_motion:=true
```

Supervised fetch test after visual grasp stack is already safe and running:

```bash
ros2 launch project_link_voice voice_direct_drive.launch.py \
  enable_motion:=true \
  enable_visual_grasp:=true
```

## Confirmation And Stop

Motion and fetch tasks always require one explicit local confirmation:

```text
确认开始
确认前往
确定开始
```

These words bypass the LLM and cancel immediately:

```text
停止
取消
急停
不要了
算了
```

Physical E-stop remains mandatory. Software cancellation is not a substitute for
power removal or the physical emergency path.
