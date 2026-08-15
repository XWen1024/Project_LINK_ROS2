# Voice LLM Tool Orchestration Runbook

This is the production voice path for Nav2 and the retained direct-drive fallback.

```text
wakeup -> 20 ms PCM -> FunVAD + Volcano bidirectional streaming ASR
-> DeepSeek official non-thinking streaming Tool Calling
-> Python safety executor -> Volcano bidirectional streaming TTS
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
export PROJECT_LINK_ASR_PROVIDER=volcano
export VOLCANO_ASR_API_KEY=...
export VOLCANO_ASR_RESOURCE_ID=volc.seedasr.sauc.duration
export VOLCANO_ASR_ENDPOINT=wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async
export DEEPSEEK_API_KEY=...
export VOLCANO_APP_ID=...
export VOLCANO_ACCESS_TOKEN=...
export VOLCANO_RESOURCE_ID=seed-tts-2.0
export VOLCANO_SPEAKER=...
export QWEATHER_API_KEY=...
```

Manual local ASR fallback:

```bash
export PROJECT_LINK_ASR_PROVIDER=faster_whisper
export PROJECT_LINK_WHISPER_MODEL=/home/wte/.cache/project_link/models/faster-whisper-small
```

The cloud ASR and local fallback are mutually exclusive per process. Volcano is
the default and does not prewarm or automatically invoke Whisper.
The launch scripts validate this before starting. Existing TTS App ID/token
values are separate credentials and may return HTTP 403 at the ASR endpoint;
prefer a dedicated `VOLCANO_ASR_API_KEY` with the configured ASR resource.

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
speech_end_to_vad
vad_to_asr_final
asr_final_to_llm_send
llm_to_tool_call
llm_api_roundtrip
llm_tool_arguments_parse
python_tool
tool_to_tts_send
tts_to_first_audio
first_audio_to_playback
speech_end_to_first_playback
tts_synthesis_complete
```

DeepSeek requests force `thinking: {type: disabled}`. Plain text deltas are
batched for no more than 80 ms, 12 characters, or the next punctuation boundary,
then sent into one Volcano bidirectional TTS session. The first PCM frame is
played immediately. After a tool call, Python executes the validated tool and
the next LLM round opens the formal TTS response. A cached `好的。` is eligible
500 ms after FunVAD ends only if no formal TTS first frame exists and the output
device is idle.

Timing rows are also printed to the console with prefix `[VOICE_TIMING]`. Follow
the persistent logs on Orin with:

```bash
tail -f ~/.ros/project_link_voice/voice_timing.jsonl
tail -f ~/.ros/project_link_voice/voice_debug.jsonl
```

Summarize recent P50/P95 values:

```bash
python3 src/project_link_voice/tools/summarize_voice_timing.py --last 20
```

Each JSONL row includes `trace_id`, local timestamp, phase, and milliseconds.
The final `timing_summary` row aggregates the phases; real asynchronous TTS may
append a second summary with `late_tts_update=true` after cloud synthesis ends.
New log files are created with mode `0600`. Set `debug_logging_enabled: false`
in the parameter YAML when recognized-text previews must not be stored while
retaining timing data.
