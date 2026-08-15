# Voice Backends Overview

Status: current
Last reviewed: 2026-08-15
Canonical branch: `main`

Project LINK has two supported, mutually exclusive voice backends:

- `classic`: FunVAD plus Volcano/faster-whisper ASR, DeepSeek Tool Calling and
  Volcano streaming TTS.
- `qwen-realtime`: Qwen3.5 Omni Flash Realtime semantic VAD, ASR, Function
  Calling and PCM output in one WebSocket session.

They share the wake serial device, microphone, speaker, `/voice/*` namespace and
robot tools. Never run both processes simultaneously. Both must keep Python as
the safety executor: named waypoints, explicit confirmation, Nav2 publisher
checks, cancellation priority and optional visual-grasp gates remain outside the
model.

The Volcengine Embedded Kit S2S work is archived under
`docs/archive/experiments/volc-s2s/` and is not a normal console backend.
