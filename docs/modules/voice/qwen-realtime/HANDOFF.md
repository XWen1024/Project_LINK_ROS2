# Qwen Realtime Voice Handoff

Status: merged into `main`, pending full robot-field validation
Last reviewed: 2026-08-15
Merge commit: `8db1799`
Archived pre-merge tag: `archive/qwen-realtime-premerge-20260814`

## Pipeline And Boundary

`project_link_qwen_realtime_voice` uses Qwen3.5 Omni Flash Realtime for semantic
VAD, streaming ASR, Function Calling and 24 kHz PCM output in one WebSocket.
The model never owns ROS Actions, services or `/cmd_vel`; the Python robot-tools
layer performs all validation and execution.

Entry modes are `pure-test`, `demo`, `nav2-dry`, `nav2` and `fetch` through
`scripts/start_qwen_realtime_voice.sh`. Published topics include `/voice/status`,
`/voice/user_text`, `/voice/assistant_text`, `/voice/tts_text` and
`/voice/realtime_event`.

## Verified State

- The merged code passes 11 direct regression scenarios and Python syntax checks.
- Orin previously built the package with DashScope SDK `1.26.5`.
- Semantic VAD used threshold `0.5`, 1200 ms silence and 300 ms prefix padding.
- Name-based iFlytek microphone selection, cached wake playback, clean Ctrl-C,
  multi-turn conversation and bounded natural exit matching were validated.
- QWeather worked with a real project host and Gzip responses.

## Remaining Gates

- Complete real Nav2 goal, cancel and arrival validation.
- Validate visual-grasp preparation and Action execution.
- Run at least 20 full-duplex external-playback and interruption cycles with the
  physical AEC reference connected.
- Confirm response cancellation and exit-reply ordering under repeated interruption.

## Runtime Data And Invariants

- Never run alongside the classic voice node.
- Secrets: `/home/wte/.config/project_link/qwen_realtime.env`
- Python environment: `/home/wte/wheeltec_robot/.venv-qwen-realtime`
- Timing: `~/.ros/project_link_qwen_realtime/voice_timing.jsonl`
- A missing or unhealthy AEC reference requires `barge_in_enabled=false`.

Detailed protocol and deployment notes are in `ORIN_GUIDE.md`.
