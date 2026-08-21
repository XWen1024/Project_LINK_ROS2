# Voice Backends Overview

Status: current
Last reviewed: 2026-08-15
Canonical branch: `main`

Project LINK has two supported, mutually exclusive voice backends:

- `classic`: FunVAD plus Volcano/faster-whisper ASR, DeepSeek Tool Calling and
  Volcano streaming TTS.
- `qwen-realtime`: Qwen3.5 Omni Flash Realtime server VAD, ASR, Function
  Calling and PCM output in one WebSocket session.

They share the wake serial device, microphone, speaker, `/voice/*` namespace and
robot tools. Never run both processes simultaneously. Both must keep Python as
the safety executor: named waypoints, explicit confirmation, Nav2 publisher
checks, cancellation priority and optional visual-grasp gates remain outside the
model.

The Volcengine Embedded Kit S2S work is archived under
`docs/archive/experiments/volc-s2s/` and is not a normal console backend.

The Ubuntu console switches the two supported backends through the typed
console-agent Action and displays sanitized JSONL timing phases. Shared operator
profiles live in `~/.config/project_link/voice_profile.json`; prompts and schemas
may be edited, but only repository-registered Python executors can be enabled.
The safety-owned `end_conversation` executor is always registered: exit intents
cancel pending/active robot work, play the fixed exit acknowledgement, close the
current realtime session and return to wake standby. Direct keyword matching
remains a fallback for exact and repeated commands such as `退出退出`. Runtime YAML
overrides remain local mode-0600 files on Orin.

Qwen Realtime also has a console-independent production entry point:

```bash
ssh orin /home/wte/wheeltec_robot/scripts/standalone/start_qwen_realtime.sh
```

It talks only to Orin `systemd --user`, relies on the service's existing conflict
with the classic backend and reports readiness from systemd. It does not require
the Ubuntu console or console-agent Action path.

The production Qwen path uses acoustic `server_vad` by default. Local PCM peak
and RMS evidence are tracked independently; when several clearly voiced chunks
arrive but the cloud VAD emits no speech event, the node commits the buffered
audio through the SDK instead of falsely reporting that the microphone was
silent. Listening begins after the wake acknowledgement by default so the
acknowledgement cannot contaminate the first user turn.
