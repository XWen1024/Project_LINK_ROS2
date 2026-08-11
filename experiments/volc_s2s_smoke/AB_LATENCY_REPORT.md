# Volc S2S WebSocket Latency A/D Report

Date: 2026-08-11

Branch: `spike/volc-s2s-ws-smoke`

Platform: Jetson Orin Nano / Ubuntu ARM64 (`aarch64`)

Embedded Kit commit: `2c94f96f3aad4094e0e818cbb031149fd4384ead`

Model observed in every `session.created`: `doubao-seed-2-0-lite-260428`

## Scope and outcome

This round measured Test A and Test D without modifying the legacy voice pipeline.
Test B was intentionally handed back to the operator for console Prompt/Tool tuning.
Test C was skipped because changing the model safely requires a controlled console
configuration change and the operator asked to focus on D.

Main results:

- Pure S2S, last input audio to first response audio: mean `2111.6 ms`, P50
  `1784 ms`, P90 `3033 ms`.
- D0 normal Function Calling continuation, tool output to first feedback audio:
  mean `2138.2 ms`.
- D2 official WebSocket `input_tts`, tool output to first feedback audio: mean
  `451.5 ms`, a `78.9%` reduction from D0.
- D3 local pre-generated PCM, tool output to playback process start: mean
  `0.4 ms`; last input audio to local feedback start: mean `1827.1 ms`.
- D3 returned `response.cancel` status `0` in all 10 runs and observed no cloud
  audio after the function output during the three-second duplicate-detection
  window.

## Method

- Every formal group contains 10 successful runs.
- Runs use real-time PCM cadence and the existing `CLOCK_MONOTONIC` logger.
- Percentiles use nearest-rank: P50 is sorted sample 5 and P90 is sorted sample 9.
- Failed attempts are preserved but excluded from statistics. The formal groups
  below all completed with 10 attempts and 10 successes.
- Test D used exactly the same Function Calling input PCM and Bot configuration.
- ASR completion was not exposed as a separate event. ASR and the first model/tool
  decision therefore remain a combined black-box interval.
- `response.created` and the first audio callback normally arrived within 0–19 ms;
  this is an SDK/event boundary and does not prove that TTS computation itself took
  0–19 ms.

## Fixed inputs

### Test A

- Text synthesized: `请只回答好`
- File: `assets/pure_s2s_answer_ok.pcm`
- Format: PCM S16LE, 16 kHz, mono
- Bytes: `50,514`
- SHA256: `9a5cd61882e42ee8ec3b84c19b02ab755053778492ce0e1336841454b705dff2`

The input file was generated and played successfully on Orin. The server did not
expose an output transcript. Response completion times varied substantially, so
the current Bot cannot be proven to have replied with only the single character
`好` in every run. Treat Test A as the current-Bot Pure S2S baseline, not a strict
single-token theoretical floor.

### Test D

- File: `assets/get_magic_number.pcm`
- Format: PCM S16LE, 16 kHz, mono
- Bytes: `86,780`
- Duration: `2.711875 s`
- SHA256: `c8e0c24793b68b0974de7a00beef3c585514c1c48206dc03f21baeb42c5ddee0`
- Expected call: `get_magic_number`, arguments `{}`
- Local tool result: `{"number":42}`

D3 local feedback:

- Text synthesized: `好了`
- File: `assets/feedback_okay.pcm`
- Format: PCM S16LE, 16 kHz, mono
- Bytes: `24,804`
- Duration: about `0.78 s`
- SHA256: `4854688d82932ad5ad5585bb20197a641ac196e9d3287a06251931b4d786a987`

## Test A: Pure S2S

| Metric | Mean | P50 | P90 | Min | Max |
|---|---:|---:|---:|---:|---:|
| Last input -> VAD stop | 693.2 | 490 | 1276 | 286 | 1906 |
| VAD stop -> first response audio | 1418.4 | 1294 | 1574 | 1074 | 2310 |
| Last input -> first response audio | 2111.6 | 1784 | 3033 | 1401 | 3380 |
| Last input -> response done | 3747.5 | 2200 | 5422 | 1677 | 7738 |

All values are milliseconds.

Raw Test A runs:

| Run | Exit | Input->VAD | VAD->first audio | Input->first audio | Input->done |
|---:|---:|---:|---:|---:|---:|
| 1 | 0 | 506 | 1574 | 2080 | 2200 |
| 2 | 0 | 286 | 1326 | 1612 | 4860 |
| 3 | 0 | 419 | 1142 | 1561 | 1677 |
| 4 | 0 | 1276 | 1284 | 2560 | 4819 |
| 5 | 0 | 441 | 1541 | 1982 | 2144 |
| 6 | 0 | 490 | 1294 | 1784 | 1988 |
| 7 | 0 | 558 | 1165 | 1723 | 1892 |
| 8 | 0 | 327 | 1074 | 1401 | 4735 |
| 9 | 0 | 1906 | 1474 | 3380 | 7738 |
| 10 | 0 | 723 | 2310 | 3033 | 5422 |

Raw artifacts:

`/home/wte/wheeltec_robot-volc-smoke/experiments/volc_s2s_smoke/artifacts/ab_latency/A_pure_s2s_response_boundary_20260811_221156`

## Test D comparison

| Strategy | Tool result -> feedback mean | P50 | P90 | Min | Max | Last input -> feedback mean | P50 | P90 | Min | Max |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| D0 normal second LLM | 2138.2 | 1946 | 2714 | 1453 | 2802 | 4498.8 | 4548 | 5245 | 3565 | 5366 |
| D1 per-response short instruction | 1849.3 | 1873 | 2190 | 1339 | 2193 | 3919.4 | 3768 | 4292 | 2812 | 6493 |
| D2 direct `input_tts` | 451.5 | 418 | 470 | 358 | 722 | 2280.2 | 2211 | 2703 | 1749 | 2880 |
| D3 local PCM | 0.4 | 0 | 1 | 0 | 1 | 1827.1 | 1818 | 2052 | 1334 | 2465 |

All values are milliseconds. D3's feedback timestamp is immediately after
successful `posix_spawnp("aplay", ...)`; it is not a hardware DAC first-sample
timestamp.

### D0 current baseline raw runs

| Run | Exit | Input->VAD | VAD->FC | Input->FC | Tool->audio | Input->audio | Input->done |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 0 | 319 | 1152 | 1471 | 1781 | 5366 | 19683 |
| 2 | 0 | 291 | 1777 | 2068 | 2279 | 4413 | 13948 |
| 3 | 0 | 272 | 1588 | 1860 | 2705 | 4569 | 16799 |
| 4 | 0 | 276 | 3237 | 3513 | 1729 | 5245 | 15784 |
| 5 | 0 | 280 | 1753 | 2033 | 2714 | 4755 | 13807 |
| 6 | 0 | 287 | 2066 | 2353 | 1946 | 4303 | 15003 |
| 7 | 0 | 550 | 1762 | 2312 | 2262 | 4581 | 25320 |
| 8 | 0 | 300 | 1811 | 2111 | 1453 | 3565 | 13684 |
| 9 | 0 | 230 | 1696 | 1926 | 1711 | 3643 | 4778 |
| 10 | 0 | 311 | 1434 | 1745 | 2802 | 4548 | 14891 |

Raw artifacts:

`/home/wte/wheeltec_robot-volc-smoke/experiments/volc_s2s_smoke/artifacts/ab_latency/D0_cloud_baseline_20260811_222044`

### D1 short second response raw runs

D1 did not change the console System Prompt. It added this instruction only to
the post-tool `response.create` request:

`工具成功后只回复“好了”，不要补充任何内容。`

The server accepted the request, but mean generated audio time remained
`10813.0 ms`, almost identical to D0's `10870.7 ms`. Therefore this protocol-local
instruction did not reliably force a short answer. The modest first-audio gain is
not strong enough to replace the operator's console-level Prompt test.

| Run | Exit | Input->VAD | VAD->FC | Input->FC | Tool->audio | Input->audio | Input->done |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 0 | 280 | 1360 | 1640 | 1339 | 2980 | 14318 |
| 2 | 0 | 303 | 1669 | 1972 | 1873 | 3848 | 16765 |
| 3 | 0 | 369 | 1058 | 1427 | 1939 | 3367 | 12545 |
| 4 | 0 | 609 | 1447 | 2056 | 1706 | 3768 | 13708 |
| 5 | 0 | 304 | 1482 | 1786 | 1961 | 3751 | 12530 |
| 6 | 0 | 304 | 1902 | 2206 | 1800 | 4011 | 13609 |
| 7 | 0 | 301 | 2049 | 2350 | 1940 | 4292 | 17452 |
| 8 | 0 | 339 | 701 | 1040 | 2193 | 6493 | 19495 |
| 9 | 0 | 521 | 736 | 1257 | 1552 | 2812 | 12333 |
| 10 | 0 | 405 | 1275 | 1680 | 2190 | 3872 | 14571 |

Raw artifacts:

`/home/wte/wheeltec_robot-volc-smoke/experiments/volc_s2s_smoke/artifacts/ab_latency/D1_cloud_short_20260811_223005`

### D2 direct WebSocket `input_tts` raw runs

The message schema comes from the official low-load sample:

`examples/low_load_solution/macos/volc_conv_ai_demo.c`

The smoke test sends `function_call_output`, does not send a manual
`response.create`, then sends a binary JSON `conversation.item.create` whose user
content is `{ "type": "input_tts", "text": "好了" }` with `interrupt_mode=1`.

All 10 runs had:

- `function=get_magic_number`, arguments `{}`
- `input_tts_sent=true`
- `response_create_sent=false`
- one short feedback audio response

| Run | Exit | Input->VAD | VAD->FC | Input->FC | Tool/input_tts->audio | Input->audio | Input->done |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 0 | 345 | 1114 | 1459 | 418 | 1881 | 2183 |
| 2 | 0 | 380 | 981 | 1361 | 722 | 2087 | 3357 |
| 3 | 0 | 587 | 1445 | 2032 | 407 | 2440 | 2645 |
| 4 | 0 | 387 | 1213 | 1600 | 464 | 2065 | 4405 |
| 5 | 0 | 1001 | 1406 | 2407 | 470 | 2880 | 4140 |
| 6 | 0 | 332 | 1411 | 1743 | 463 | 2211 | 3563 |
| 7 | 0 | 290 | 1682 | 1972 | 358 | 2336 | 3586 |
| 8 | 0 | 337 | 1655 | 1992 | 454 | 2450 | 2691 |
| 9 | 0 | 320 | 1061 | 1381 | 366 | 1749 | 4123 |
| 10 | 0 | 259 | 2050 | 2309 | 393 | 2703 | 3989 |

Raw artifacts:

`/home/wte/wheeltec_robot-volc-smoke/experiments/volc_s2s_smoke/artifacts/ab_latency/D2_input_tts_20260811_223331`

### D3 local pre-generated PCM raw runs

The smoke test sends `function_call_output`, calls the official `volc_interrupt`
API, starts `aplay` for the fixed PCM, and waits three seconds to detect a duplicate
cloud response.

All 10 runs had:

- `function=get_magic_number`, arguments `{}`
- `cloud_response_cancel status=0`
- `local_playback_finished=true`
- `cloud_audio_after_function=false`

| Run | Exit | Input->VAD | VAD->FC | Input->FC | Tool->local start | Input->local start | VAD->local start | Playback duration |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 0 | 289 | 1220 | 1509 | 0 | 1516 | 1227 | 938 |
| 2 | 0 | 274 | 2189 | 2463 | 0 | 2465 | 2191 | 915 |
| 3 | 0 | 298 | 1516 | 1814 | 1 | 1818 | 1520 | 915 |
| 4 | 0 | 418 | 1385 | 1803 | 1 | 1857 | 1439 | 914 |
| 5 | 0 | 342 | 1311 | 1653 | 0 | 1654 | 1312 | 915 |
| 6 | 0 | 276 | 1699 | 1975 | 0 | 1976 | 1700 | 914 |
| 7 | 0 | 299 | 1748 | 2047 | 1 | 2052 | 1753 | 915 |
| 8 | 0 | 329 | 1667 | 1996 | 0 | 2001 | 1672 | 915 |
| 9 | 0 | 313 | 1019 | 1332 | 1 | 1334 | 1021 | 913 |
| 10 | 0 | 343 | 1252 | 1595 | 0 | 1598 | 1255 | 915 |

Raw artifacts:

`/home/wte/wheeltec_robot-volc-smoke/experiments/volc_s2s_smoke/artifacts/ab_latency/D3_local_pcm_20260811_223458`

## Answers to the requested questions

### A. How fast is the S2S path itself?

With the current Bot and fixed Pure S2S PCM, last input to first response audio was
`2111.6 ms` mean, `1784 ms` P50, and `3033 ms` P90. Server VAD tail contributed
`693.2 ms` mean; the remaining VAD-stop-to-audio black box contributed `1418.4 ms`
mean. A separate ASR-complete event was not available.

### B. How much can Prompt/Tool tuning reduce FC decision time?

Not measured in this run. The operator owns the console B0/B1 comparison. D1 is
not a substitute because its instruction is sent only after the Function Call has
already been chosen.

### C. Which model is fastest for Function Calling?

Not measured. The session remained on `doubao-seed-2-0-lite-260428` for every run.
No model or `service_tier` setting was changed or guessed.

### D. Can the second LLM be bypassed?

Yes.

- D2 proves the official low-load WebSocket path can send direct `input_tts` after
  tool success. It reduced tool-result-to-first-audio mean from `2138.2 ms` to
  `451.5 ms` while preserving cloud voice output.
- D3 proves local fixed PCM can start essentially immediately after tool success
  and that `volc_interrupt` can suppress the cloud continuation in this test.
- For production migration, D2 is the preferred general success-feedback path
  when cloud voice consistency matters. D3 is appropriate only for a small set of
  fixed, pre-approved phrases where the lowest perceived latency is more important
  than dynamic wording.

## Limitations

- No separate ASR completion, LLM start, or TTS start event was exposed.
- Test A output text was not transcribed, so exact reply wording is unverified.
- D3 measures process start, not the first sample leaving the physical speaker.
- D3 duplicate suppression was observed for three seconds per run; it is not a
  proof about arbitrarily delayed server behavior.
- Test B and Test C remain pending controlled console configuration work.
- No production bridge or legacy voice module was changed.
