# Classic Voice Backend Handoff

Status: current, partial hardware validation
Last reviewed: 2026-08-15
Canonical branch: `main`

## Pipeline

```text
iFlytek serial wake
-> 20 ms microphone PCM
-> FunVAD endpointing + selected ASR
-> DeepSeek streaming Tool Calling
-> Python safety and task execution
-> Volcano bidirectional streaming TTS
```

Production entrypoint is `scripts/start_voice_nav2_stack.sh`. Core ROS interfaces
are `/voice/text_input`, `/voice/status`, `/voice/tts_text`, Nav2
`/navigate_to_pose` and optional `/visual_grasp/track_and_grasp`.

## Verified State

- Previous Orin validation reported 51 passing tests.
- Serial wake, USB microphone, faster-whisper, DeepSeek Tool Calling, Volcano
  TTS, fixed phrase cache, multi-turn conversation and local exit handling worked.
- Observed DeepSeek first text was approximately `1.65 s`; Volcano TTS first
  audio packet was approximately `272-277 ms` in the recorded runs.
- Stop/cancel/exit phrases bypass the LLM and return to wake waiting.

## Current Gaps

- The last inspected Orin private environment did not contain valid
  `VOLCANO_ASR_*` credentials, so the actual provider remained
  `faster_whisper` even though Volcano is the configured production preference.
- Complete two-turn plus silence-exit audio acceptance remains required.
- Confirmed real Nav2 motion followed by `TrackAndGrasp` is not yet an accepted
  end-to-end field result.

## Invariants And Runtime Data

- Models can only request registered tools; they never publish velocity or enable torque.
- Navigation uses saved names, never generated coordinates, and requires exact
  local confirmation.
- Silence-only conversation exit must not cancel an already executing robot task.
- Secrets: `/home/wte/.config/project_link/voice_api.env`
- Timing: `~/.ros/project_link_voice/voice_timing.jsonl`
- Debug: `~/.ros/project_link_voice/voice_debug.jsonl`
- Waypoints: `~/.ros/project_link_voice/waypoints.json`

Detailed orchestration is in `ORCHESTRATION.md`; measurement rules are in
`../LATENCY_VALIDATION.md`.
