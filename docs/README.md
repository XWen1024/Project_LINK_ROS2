# Project LINK Documentation

This directory is organized by authority and module. Start from the current
handoff for the subsystem you are changing; use archived material only for
historical evidence.

## Architecture

- `architecture/SYSTEM_OVERVIEW.md`: current robot and computer boundaries.
- `architecture/CONSOLE_ARCHITECTURE.md`: Ubuntu control-console design.
- `decisions/0001-console-foundation.md`: accepted console technology choices.

## Current Modules

- Navigation: `modules/navigation/HANDOFF.md`
- Manipulation: `modules/manipulation/HANDOFF.md`
- Voice overview: `modules/voice/OVERVIEW.md`
- Classic voice: `modules/voice/classic/HANDOFF.md`
- Qwen Realtime: `modules/voice/qwen-realtime/HANDOFF.md`
- UWB: `modules/uwb/HANDOFF.md`
- VL53L0X: `modules/sensors/vl53l0x/HANDOFF.md`
- Fall response:
  - Android to Orin backend: `modules/sensors/fall-response/ANDROID_ORIN_HANDOFF.md`
  - Legacy voice trigger: `modules/sensors/fall-response/VOICE_INTEGRATION.md`

## Runbooks And Archives

- Site runbooks live under `runbooks/`.
- Superseded handoffs and experiments live under `archive/`.
- Archived documents are evidence snapshots and must not be edited as current
  operating instructions.

Every current handoff should record its verification date, canonical branch or
commit, remaining hardware gates, and the documents it supersedes.
