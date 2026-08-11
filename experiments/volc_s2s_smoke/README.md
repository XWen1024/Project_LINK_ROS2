# Volcengine low-load WebSocket S2S smoke test

This directory is an isolated architecture Spike for the official
`volc_conv_ai` low-load WebSocket transport. It does not import, modify, start,
or replace Project LINK's existing voice pipeline.

## Environment

- Device: NVIDIA Jetson Orin Nano
- OS: Ubuntu 22.04.5 LTS (Jammy), Linux `5.15.185-tegra`
- Architecture: native `aarch64`
- GCC/G++: 11.4.0
- CMake: 3.22.1
- Python: 3.10.12 (not used by this smoke program)
- Embedded Kit commit: `2c94f96f3aad4094e0e818cbb031149fd4384ead`
- mbedTLS: v3.6.3 source at commit
  `22098d41c6620ce07cf8a0134d37302355e1e5ef`
- Transport: `ENABLE_WS_MODE`, with no RTC source or library linked

The official commit's prebuilt library inspection found:

| Artifact | Actual architecture | Used |
| --- | --- | --- |
| `linux_x64/libVolcEngineRTCLite.so` | ELF x86-64 | No |
| SDK `third_party/prebuilt/zlib/lib/libz.a` | Mach-O arm64 (macOS) | No |
| Ubuntu `/lib/aarch64-linux-gnu/libz.so` | ELF AArch64 | Yes |
| mbedTLS | Built from pinned source on Orin | Yes |

## Source/API findings

At the pinned SDK commit, the public API is:

- `volc_create`
- `volc_start` with `VOLC_MODE_WS`
- `volc_stop` / `volc_destroy`
- `volc_send_audio_data`
- `volc_send_message`
- callbacks for event, conversation status, audio, video, and message data

The low-load transport maps server events to these conversation states:

- `input_audio_buffer.speech_started` -> `VOLC_CONV_STATUS_LISTENING`
- `input_audio_buffer.speech_stopped` -> `VOLC_CONV_STATUS_THINKING`
- first `response.audio.delta` -> `VOLC_CONV_STATUS_ANSWERING`
- completed `response.done` -> `VOLC_CONV_STATUS_ANSWER_FINISH`

The official low-load macOS sample uses PCM16, 16 kHz, mono, and 3200-byte
(100 ms) frames. This smoke test uses that exact format by default and commits
on the final input frame.

## Build

Initialize the pinned SDK submodule, then build on the Jetson itself:

```bash
git submodule update --init --recursive
cd experiments/volc_s2s_smoke
./scripts/build.sh
```

The build script does not install system packages. It requires the existing
compiler toolchain, CMake, Git, pkg-config, binutils, and Ubuntu's native
`zlib1g-dev`. It fetches and compiles pinned mbedTLS source inside the CMake
build directory. If zlib development files are missing, the script reports the
suggested package and stops; it does not run `sudo`.

Expected architecture evidence:

```text
ELF 64-bit LSB pie executable, ARM aarch64
Machine: AArch64
PASS: native ARM64 binary built .../volc_ws_smoke
```

## Credentials

The SDK commit requires these actual fields for device dynamic registration and
the WebSocket session:

```text
VOLC_BOT_ID
VOLC_INSTANCE_ID
VOLC_PRODUCT_KEY
VOLC_PRODUCT_SECRET
VOLC_DEVICE_NAME
```

The dedicated local credential file is `.env.local`. It is ignored by Git and
is loaded automatically by `scripts/run_smoke.sh`. Fill it locally without
printing values:

```bash
cd experiments/volc_s2s_smoke
chmod 600 .env.local
${EDITOR:-nano} .env.local
```

The smoke program and wrapper print only presence checks. Do not put real
values in this README, shell examples, source code, or committed artifacts.

## Run

Connection-only test:

```bash
cd experiments/volc_s2s_smoke
./scripts/run_smoke.sh
```

The successful path includes:

```text
architecture=aarch64
transport=websocket_low_load
authentication_registration_result code=0
connecting transport=websocket
sdk_event code=1 name=VOLC_EV_CONNECTED
connected transport=websocket
```

The wrapper saves combined SDK/program output to `artifacts/smoke.log` with
private file permissions.

## PCM test

Input must be headerless PCM S16LE, 16 kHz, mono. A WAV file must have its
header removed/decoded before use. Run:

```bash
./scripts/run_smoke.sh --pcm /absolute/path/test_input.pcm
```

The program sends audio at the official sample's 100 ms realtime cadence. A
different cadence can be tested explicitly, for example `--frame-ms 20`, but
the input format remains 16 kHz mono S16LE.

AI audio is saved directly as:

```text
artifacts/response.pcm
artifacts/response.wav
```

The WAV wrapper uses the response format assumed by the official low-load
sample: PCM S16LE, 16 kHz, mono. Playback options are checked on the actual
Jetson during the online phase. Typical commands are:

```bash
aplay -f S16_LE -r 16000 -c 1 artifacts/response.pcm
aplay artifacts/response.wav
```

## Latency output

All elapsed timings use `CLOCK_MONOTONIC`. The program reports:

```text
T0_websocket_connect_start
T1_websocket_connected
T2_first_input_audio_sent
T3_last_input_audio_sent
T4_speech_started
T5_speech_stopped
T6_first_ai_audio
T7_response_done
connect_ms
speech_end_to_first_audio_ms
response_total_ms
```

Missing events are printed as `N/A`; the program never synthesizes timestamps.

## Function Calling test

Do this only after connection and PCM-to-AI-audio have passed. In the
Volcengine hardware conversational agent console, configure the same Bot for
the account/device credentials and enable the account/Bot's mixed orchestration
or Function Calling capability. Define one local function:

```text
name: get_magic_number
description: Return the fixed magic number.
parameters: an empty object
```

Prepare 16 kHz mono S16LE speech such as “请告诉我神奇数字。” and run:

```bash
./scripts/run_smoke.sh \
  --pcm /absolute/path/get_magic_number.pcm \
  --expect-function-call \
  --response-timeout-sec 60
```

Local PCM/WAV files under `assets/` are ignored by Git so recorded speech is
not committed accidentally.

The current official low-load sample receives
`conversation.item.created`/`response.function_call_arguments.done`. The
official high-quality sample documents the return item as:

```json
{
  "type": "conversation.item.create",
  "item": {
    "call_id": "...",
    "type": "function_call_output",
    "object": "realtime.item",
    "output": "{\"number\":42}"
  }
}
```

This smoke test accepts both those Realtime WebSocket events and the repository's
older `tool_calls` envelope, returns the fixed result on the same WebSocket,
then sends `response.create` for text+audio continuation. It is deliberately
not a general tool dispatcher. Sanitized raw function events and the returned
result are saved to `artifacts/function_calls.jsonl`.

If ordinary S2S works but Function Calling does not, classify the evidence as:

- SDK received the call but Bot/business configuration is wrong.
- Account/Bot mixed orchestration is not enabled.
- This low-load transport/service combination does not support the capability.
- Documentation and pinned SDK behavior disagree.
- A device-side implementation bug exists.

Do not replace the failed test with a separate Ark connection or parallel
home-grown orchestration.

## Known limitations

- RTC transport is not tested and the x86-64 RTC library is never linked.
- Real USB microphone capture is not implemented.
- No Python or Unix-domain-socket bridge is implemented.
- No existing ASR, DeepSeek, TTS, wake-word, VAD, ROS, or robot module is used.
- Mixed orchestration requires account/Bot console configuration that cannot be
  inferred or created by this source tree.
- The official Linux OSAL reports the platform string as `macos`; this affects
  the SDK User-Agent label but is not patched unless runtime evidence shows it
  blocks the service.

See [SMOKE_REPORT.md](SMOKE_REPORT.md) for the observed pass/fail evidence.

For the three-run end-to-end and phase latency measurements, see
[LATENCY_REPORT.md](LATENCY_REPORT.md). Re-run the same benchmark with:

```bash
./scripts/run_latency_3x.sh assets/get_magic_number.pcm
```
