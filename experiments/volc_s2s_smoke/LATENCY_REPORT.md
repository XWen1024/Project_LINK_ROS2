# Volcengine low-load WebSocket latency report

## Result

Three identical native Orin ARM64 Function Calling runs completed successfully.
The measured latency from the final input PCM frame being sent to the first
audio frame of the final AI answer was:

```text
run 1: 3327 ms
run 2: 4120 ms
run 3: 4120 ms
mean : 3855.7 ms
min  : 3327 ms
max  : 4120 ms
```

All three runs received `get_magic_number`, returned `{"number":42}` on the
same WebSocket, received final AI audio, reached `response.done=completed`, and
exited with code `0`.

## Test conditions

- Device: NVIDIA Jetson Orin Nano, Ubuntu 22.04.5, native aarch64
- SDK commit: `2c94f96f3aad4094e0e818cbb031149fd4384ead`
- Transport: official `volc_conv_ai` low-load WebSocket
- Model reported by server: `doubao-seed-2-0-lite-260428`
- Turn detection: server VAD
- Input: PCM S16LE, 16 kHz, mono, 86,780 bytes, 2.711875 seconds
- Input SHA-256:
  `c8e0c24793b68b0974de7a00beef3c585514c1c48206dc03f21baeb42c5ddee0`
- Command semantic: “请告诉我神奇数字。”
- Timing clock: `CLOCK_MONOTONIC`, millisecond resolution

The first final audio timestamp is deliberately taken only after the local
Function Call result has been returned. This excludes any Bot welcome audio
that may overlap the input upload.

## Three-run measurements

All values are milliseconds.

| Phase | Run 1 | Run 2 | Run 3 | Mean | Min | Max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Device registration | 1348 | 331 | 319 | 666.0 | 319 | 1348 |
| WSS connect | 386 | 390 | 369 | 381.7 | 369 | 390 |
| Input end → server VAD stop | 868 | 267 | 356 | 497.0 | 267 | 868 |
| VAD stop → Function Call | 1060 | 1741 | 1663 | 1488.0 | 1060 | 1741 |
| Input end → Function Call | 1928 | 2008 | 2019 | 1985.0 | 1928 | 2019 |
| Function Call → arguments done | 2 | 4 | 6 | 4.0 | 2 | 6 |
| Local function/result send | 0 | 0 | 0 | 0.0 | 0 | 0 |
| Function output → response created | 1397 | 2108 | 2095 | 1866.7 | 1397 | 2108 |
| Response created → first final audio | 0 | 0 | 0 | 0.0 | 0 | 0 |
| **Input end → first final AI audio** | **3327** | **4120** | **4120** | **3855.7** | **3327** | **4120** |
| First final audio → audio done | 10558 | 12599 | 10339 | 11165.3 | 10339 | 12599 |
| Input end → response done | 13885 | 16719 | 14459 | 15021.0 | 13885 | 16719 |

Device registration and WSS connection are session setup costs. They are not
part of the per-turn `input end → first final AI audio` result when the
WebSocket is kept open.

## Observable latency decomposition

The mean first-audio path is approximately:

```text
final input frame sent
  497.0 ms  server VAD tail / end-of-speech decision
 1488.0 ms  ASR + initial LLM reasoning + tool selection (combined black box)
    4.0 ms  Function Call item → arguments complete
    0.0 ms  local get_magic_number + WebSocket result send (below 1 ms resolution)
 1866.7 ms  function result → final response/first audio
-----------
 3855.7 ms  final input frame → first final AI audio
```

The arithmetic uses the event boundaries actually exposed by the SDK and
server. It is not an estimate derived from wall-clock timestamps.

## What cannot be separated

### ASR versus initial LLM/tool selection

No transcription-completed event was delivered in any of the three runs:

```text
asr_event_observed=false
```

The low-load SDK exposes `speech_stopped` and the subsequent
`conversation.item.created(function_call)`, but no intermediate ASR completion
boundary. Therefore the measured `1488.0 ms` is the combined cloud interval:

```text
ASR finalization + LLM/tool decision + Function Call creation
```

It would be incorrect to label the whole interval as either pure ASR or pure
LLM latency.

### Final LLM continuation versus TTS startup

After the Function Call output, `response.created` and the first returned audio
arrived in the same millisecond in all three runs. This does not mean TTS took
zero time. It means the protocol did not expose an earlier text/LLM-complete or
TTS-start event before the first audio callback.

Consequently the measured `1866.7 ms` interval is the combined black box:

```text
server receives function output
+ final LLM continuation/formulation
+ TTS startup
+ network delivery of first PCM frame
```

## Variability

- First-audio range: `793 ms` across three runs.
- The dominant variable phases were the server VAD tail, initial cloud
  ASR/tool decision, and post-function cloud continuation.
- Local Function Calling overhead was negligible at millisecond resolution.
- Full response completion varied with generated speech length; it should not
  be confused with first-audio responsiveness.
- Registration run 1 was an outlier at `1348 ms`; later runs were `331/319 ms`.

Three samples establish repeatability for this Spike but are not enough for
production percentile claims such as p95 or p99.

## Raw artifacts

Orin directory:

```text
/home/wte/wheeltec_robot-volc-smoke/experiments/volc_s2s_smoke/
  artifacts/latency_3x_20260811/
    latency_runs.tsv
    latency_summary.tsv
    run_1/smoke.log
    run_1/function_calls.jsonl
    run_2/smoke.log
    run_2/function_calls.jsonl
    run_3/smoke.log
    run_3/function_calls.jsonl
```

The runtime artifacts contain no credential values and remain outside Git.

## Conclusion

For the tested Function Calling command, the official low-load WebSocket path
on Jetson Orin Nano delivered the first final AI audio in approximately
`3.86 seconds` on average after the last input audio frame was sent. The cloud
protocol available at this SDK commit does not expose enough events to split
ASR from initial LLM/tool selection, or final LLM continuation from TTS startup;
the report records the narrowest defensible combined intervals instead.

