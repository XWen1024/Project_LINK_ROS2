# Voice LLM Tool Orchestration Runbook

This is the production voice path for the current no-Nav2 direct-drive phase.

```text
wakeup -> FunVAD -> faster-whisper -> DeepSeek official Tool Calling
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
export DEEPSEEK_API_KEY=...
export VOLCANO_APP_ID=...
export VOLCANO_ACCESS_TOKEN=...
export VOLCANO_RESOURCE_ID=seed-tts-2.0
export VOLCANO_SPEAKER=...
export QWEATHER_API_KEY=...
```

The voice nodes default to `https://api.deepseek.com` with model
`deepseek-v4-flash`. `SILICONFLOW_API_KEY` is not used by the voice LLM path;
it remains separate for the fall-response vision module.

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

## Debug And Timing Logs

Both `voice_dialog_node` and `llm_motion_demo_node` assign a short `trace_id` to
every microphone or text-topic interaction. Defaults:

```yaml
debug_logging_enabled: true
timing_debug_enabled: true
timing_console_enabled: true
debug_log_file: ~/.ros/project_link_voice/voice_debug.jsonl
timing_log_file: ~/.ros/project_link_voice/voice_timing.jsonl
```

The ordinary debug file records state transitions and a maximum 120-character
text preview. The timing file avoids full recognized text and records phases
such as:

```text
vad_record
asr
llm_api_roundtrip
llm_response_parse
llm_tool_arguments_parse
python_tool
llm_total
tts_dispatch
tts_first_audio
tts_synthesis_complete
```

Timing rows are also printed to the console with prefix `[VOICE_TIMING]`. Follow
the persistent logs on Orin with:

```bash
tail -f ~/.ros/project_link_voice/voice_timing.jsonl
tail -f ~/.ros/project_link_voice/voice_debug.jsonl
```

Each JSONL row includes `trace_id`, local timestamp, phase, and milliseconds.
The final `timing_summary` row aggregates the phases; real asynchronous TTS may
append a second summary with `late_tts_update=true` after cloud synthesis ends.
New log files are created with mode `0600`. Set `debug_logging_enabled: false`
in the parameter YAML when recognized-text previews must not be stored while
retaining timing data.
