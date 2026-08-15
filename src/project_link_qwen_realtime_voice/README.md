# Project LINK Qwen Realtime Voice

This package is an independent alternative to `project_link_voice`. It uses one
Qwen3.5 Omni Realtime WebSocket session for server VAD, streaming ASR, Function
Calling, text generation, and streaming PCM output. Never start both voice nodes
at the same time because they share the wake serial port, microphone, speaker,
and robot tools.

## Orin Environment

```bash
cd /home/wte/wheeltec_robot
source /opt/ros/humble/setup.bash
python3 -m venv --system-site-packages .venv-qwen-realtime
source .venv-qwen-realtime/bin/activate
source scripts/project_link_env.sh
source /home/wte/.config/project_link/qwen_realtime.env
python -m pip install -r src/project_link_qwen_realtime_voice/requirements-orin.txt
colcon build --packages-select project_link_qwen_realtime_voice
source install/setup.bash
```

Create `/home/wte/.config/project_link/qwen_realtime.env` from the packaged
example and set at least:

```bash
export DASHSCOPE_API_KEY=...
export QWEN_REALTIME_ENDPOINT=wss://WORKSPACE_ID.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime
export QWEN_REALTIME_MODEL=qwen3.5-omni-flash-realtime
export QWEN_REALTIME_VOICE=Ethan
```

The model defaults to 16 kHz mono PCM input, 24 kHz mono PCM output,
`semantic_vad`, threshold `0.5`, silence `1200 ms`, and prefix padding `300 ms`.
The node logs the server `session.updated` echo; treat that echo as the source of
truth instead of SDK defaults.

## Start

```bash
cd /home/wte/wheeltec_robot
bash scripts/start_qwen_realtime_voice.sh pure-test
bash scripts/start_qwen_realtime_voice.sh demo
bash scripts/start_qwen_realtime_voice.sh nav2-dry
bash scripts/start_qwen_realtime_voice.sh nav2
bash scripts/start_qwen_realtime_voice.sh fetch
```

- `pure-test`: realtime conversation and tools without motion.
- `demo`: bounded short `/cmd_vel` actions without SLAM.
- `nav2-dry`: named-waypoint confirmation without sending a Nav2 goal.
- `nav2`: confirmed named-waypoint Nav2 navigation.
- `fetch`: confirmed Nav2 navigation followed by `TrackAndGrasp`.

The first wake acknowledgement is generated through Qwen and saved as raw
24 kHz mono PCM at `~/.cache/project_link_qwen_realtime/wakeup_ack.pcm`. Later
wakes play the local cache before opening microphone upload.

## Safety

- Qwen never receives a ROS publisher or Action client.
- Navigation and fetch tools only create a pending task.
- Python requires an exact local confirmation phrase before sending a goal.
- Stop/cancel/exit words locally cancel model output, navigation, grasp, and
  demo motion.
- Nav2 mode rejects unexpected `/cmd_vel` publishers.
- Full-duplex interruption is enabled by default and assumes the iFlytek board
  receives the physical speaker AEC reference. Set `barge_in_enabled:=false`
  if the hardware AEC loop is not operating correctly.

Timing events are printed with `[QWEN_TIMING]` and written to
`~/.ros/project_link_qwen_realtime/voice_timing.jsonl`.
