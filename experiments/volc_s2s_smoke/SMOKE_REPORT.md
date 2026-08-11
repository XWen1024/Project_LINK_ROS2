# Volcengine Embedded Kit low-load WS smoke report

## Scope

Native Jetson Orin Nano / Ubuntu aarch64 validation of official SDK commit
`2c94f96f3aad4094e0e818cbb031149fd4384ead`, with WebSocket enabled and RTC
disabled. This Spike is isolated from the existing Project LINK voice chain.

## Isolation evidence

- Original user workspace: `main` at
  `00cdaa4fccc0430c53d69711b60e40873ce5afba`, with pre-existing uncommitted
  visual-grasp/VL53L0X work; it was not stashed, reset, or edited by this Spike.
- Windows Spike worktree:
  `C:\Users\XWen1024\Documents\ROS2小车-volc-smoke`
- Orin Spike worktree: `/home/wte/wheeltec_robot-volc-smoke`
- Branch: `spike/volc-s2s-ws-smoke`

## Platform evidence

```text
Linux ubuntu 5.15.185-tegra ... aarch64 GNU/Linux
Ubuntu 22.04.5 LTS
gcc/g++ 11.4.0
cmake 3.22.1
Python 3.10.12
```

## Dependency architecture evidence

- Official Linux RTC shared library: x86-64; rejected and not linked.
- Official bundled zlib archive: Mach-O arm64 for macOS; rejected and not linked.
- Ubuntu system zlib: AArch64; selected.
- mbedTLS 3.6.3: pinned source commit
  `22098d41c6620ce07cf8a0134d37302355e1e5ef`; selected for native build.

## Results

- [PASS] ARM64 compile
  - Observed: `libvolc_conv_ai_ws.a` and `volc_ws_smoke` built successfully on
    the Orin with GCC 11.4.0. `file` reports `ELF 64-bit ... ARM aarch64`, and
    `readelf -h` reports `Machine: AArch64`.
  - Expected: native AArch64 WS-only SDK library and CLI.
  - Error: none. Official source emits non-fatal format/thread signature
    warnings; they are retained as evidence rather than hidden.
  - Probable layer: native portability baseline passed.
  - Next action: proceed to the Function Calling gate after the successful
    WSS/S2S validation recorded below.

Additional binary checks:

```text
Class: ELF64
Machine: AArch64
RTC_SYMBOLS_ABSENT
X86_RTC_REFERENCE_ABSENT
```

`ldd` resolves only native AArch64 glibc/loader at runtime; mbedTLS is linked
from the native static build, and no RTC library is present.

- [PASS] WS initialization
  - `volc_create` and `volc_start(VOLC_MODE_WS)` returned success on Orin.
  - The server returned `session.created` with model
    `doubao-seed-2-0-lite-260428`, PCM16 input/output, and `server_vad`.

- [PASS] TLS/WSS connect
  - Connected natively to `ai-gateway.vei.volces.com:443` through the official
    low-load transport. A connection-only run measured `2422 ms`; the final
    PCM run measured `582 ms`. Both shut down cleanly.

- [PASS] authentication
  - Official dynamic device registration completed successfully. The final PCM
    run measured `367 ms`. Credential values were loaded from ignored mode-600
    `.env.local` and were not printed by the smoke wrapper.

- [PASS] PCM upload
  - Uploaded all `222,420` bytes of the official `hi_lexin.pcm` asset as PCM
    S16LE, 16 kHz, mono, using 100 ms realtime cadence and a final commit.

- [PASS] server speech detection
  - Received actual `VOLC_CONV_STATUS_LISTENING`, `THINKING`, `ANSWERING`, and
    `ANSWER_FINISH` callbacks, plus `input_audio_buffer.committed` events.

- [PASS] AI audio received
  - Received `987,554` bytes through `on_volc_audio_data` and a completed
    `response.done` event. The full PCM run exited with code `0`.

- [PASS] AI audio playable
  - Generated `response.wav` with `987,598` bytes. `soxi` and `ffprobe` report
    PCM S16LE, 16 kHz, mono, duration `30.861062 s`; `aplay -D null` accepted
    and played the complete file without an error.

- [PASS] mixed orchestration
  - The corrected Bot definition caused the server to emit a Function Call,
    accept the local result, and continue to a completed audio response over
    the same low-load WebSocket session. No separate Ark connection or callback
    relay was used.

- [PASS] function call received
  - Received both `conversation.item.created` with `item.type=function_call`
    and `response.function_call_arguments.done`.
  - Observed call ID: `call_9mfspjhprb6acvp15nyjz5tz`.
  - Observed function name: `get_magic_number`.
  - Observed arguments: `{}`.

- [PASS] function output returned
  - Returned `conversation.item.create` with the same `call_id`, item type
    `function_call_output`, and output `{"number":42}`.
  - The SDK send succeeded and the follow-up `response.create` was sent on the
    same WebSocket.

- [PASS] final AI response
  - Received AI PCM after the Function Call result, followed by
    `response.done` with status `completed`. The Level 4 process exited `0`.

The completed online runs used `VOLC_BOT_ID`, `VOLC_INSTANCE_ID`,
`VOLC_PRODUCT_KEY`, `VOLC_PRODUCT_SECRET`, and `VOLC_DEVICE_NAME` from ignored
`.env.local`. No credential values are stored in this report. Function Calling
was validated with the corrected Bot/account console configuration.

## Current blocker

None for the defined Spike. Levels 1 through 4 pass with the official low-load
WebSocket transport on native Orin ARM64.

## First credentialed runs

- Connection-only WSS test passed: device registration completed in `373 ms`,
  WebSocket connected in `2422 ms`, and shutdown was clean.
- The first PCM run reached the service, received `session.created`, server VAD
  LISTENING/THINKING states, and `54,976` bytes of PCM response audio.
- That run stopped at the final commit because the CLI initially treated the
  SDK's positive WebSocket byte-count return (`69`) as an error. Official
  low-load code returns a positive send length from the commit-triggered
  `response.create`; only negative values indicate failure. The CLI was corrected
  without patching the official SDK.
- The repeated complete run passed PCM upload, AI PCM receive, WAV generation,
  structural/playback validation, and clean shutdown.
- The first Function Calling attempt exposed a console typo:
  `get_magic_numbe`. The strict local whitelist rejected it fail-closed, which
  classified the failure as Case A (Bot/business configuration) without adding
  a typo alias. After the console name was corrected to `get_magic_number`, the
  repeated Level 4 run passed end to end.

## Final Level 3 timing sample

```text
connect_ms=582
T2_first_input_audio_sent=968 ms
T3_last_input_audio_sent=7870 ms
T4_speech_started=3155 ms
T5_speech_stopped=3693 ms
T6_first_ai_audio=2326 ms
T7_response_done=37885 ms
response_total_ms=30015
```

`speech_end_to_first_audio_ms` is `N/A` because the Bot produced audio before
the first observed speech-stop event (welcome/overlapping server-VAD turns).
Therefore this bundled multi-utterance asset proves transport and audio flow but
is not a clean single-turn latency benchmark.

## Level 4 Function Calling evidence

Input file:

```text
assets/get_magic_number.pcm
PCM S16LE / 16000 Hz / mono
86780 bytes / 2.711875 seconds
```

Observed sequence:

```text
conversation.item.created
  item.type=function_call
  item.name=get_magic_number
  item.call_id=call_...

response.function_call_arguments.done
  arguments={}

conversation.item.create
  item.type=function_call_output
  item.call_id=<same call_id>
  item.output={"number":42}

response.create
response audio
response.done status=completed
```

Level 4 metrics:

```text
authentication_registration_ms=1168
connect_ms=399
T2_first_input_audio_sent=1569 ms
T3_last_input_audio_sent=4278 ms
T4_speech_started=3189 ms
T5_speech_stopped=4635 ms
T6_first_ai_audio=2862 ms
T7_response_done=13551 ms
response_total_ms=9273
total_audio_bytes=148472
```

The final `response.wav` is PCM S16LE, 16 kHz, mono, `4.639750 s`, and passed
`soxi`, `ffprobe`, and `aplay -D null`. Sanitized Function Call evidence contains
three JSONL records in `artifacts/function_calls.jsonl`: call notification,
arguments completion, and same-session output return.

## Timing evidence

The Level 3 and Level 4 samples above were recorded with `CLOCK_MONOTONIC`.
Missing or temporally invalid derived events remain `N/A`; no latency value is
synthesized.

## Artifact locations

```text
artifacts/smoke.log
artifacts/response.pcm
artifacts/response.wav
artifacts/function_calls.jsonl
```

Runtime artifacts are ignored by Git except for `artifacts/.gitkeep`.
