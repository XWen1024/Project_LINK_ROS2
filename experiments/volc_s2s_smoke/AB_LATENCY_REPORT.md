# Volc S2S WebSocket Latency A/B/C/D Report

Date: 2026-08-12

Branch: `spike/volc-s2s-ws-smoke`

Platform: Jetson Orin Nano / Ubuntu ARM64 (`aarch64`)

Embedded Kit commit: `2c94f96f3aad4094e0e818cbb031149fd4384ead`

Models observed in `session.created`:

- Tests A/B/D: `doubao-seed-2-0-lite-260428`
- Test C Turbo candidate: `doubao-seed-2-1-turbo-260628`

## Scope and outcome

This round measured Tests A, B, D, and the first Test C model candidate without
modifying the legacy voice pipeline. The operator switched the console Prompt/Tool
configuration for B1 and switched the model for Test C.

Main results:

- Pure S2S, last input audio to first response audio: mean `2111.6 ms`, P50
  `1784 ms`, P90 `3033 ms`.
- D0 normal Function Calling continuation, tool output to first feedback audio:
  mean `2138.2 ms`.
- D2 official WebSocket `input_tts`, tool output to first feedback audio: mean
  `451.5 ms`, a `78.9%` reduction from D0.
- D3 local pre-generated PCM, tool output to playback process start: mean
  `0.4 ms`; last input audio to local feedback start: mean `1827.1 ms`.
- B1 minimal Prompt/Tool schema did not improve FC latency: VAD-stop-to-call mean
  was `2144.4 ms`, P50 `1424 ms`, and P90 `3370 ms`.
- Against the same B1 Prompt/Tool and PCM, Seed 2.1 Turbo reduced mean
  VAD-stop-to-call latency by `212.3 ms` (`9.9%`) and P90 by `924 ms` (`27.4%`),
  but its P50 was `494 ms` slower. The observed advantage is tail-latency
  reduction, not a universal per-turn speedup.
- D3 returned `response.cancel` status `0` in all 10 runs and observed no cloud
  audio after the function output during the three-second duplicate-detection
  window.

## Method

- Every formal group contains 10 successful runs.
- Runs use real-time PCM cadence and the existing `CLOCK_MONOTONIC` logger.
- Percentiles use nearest-rank: P50 is sorted sample 5 and P90 is sorted sample 9.
- Failed attempts are preserved but excluded from latency statistics. B1 and the
  Turbo group each required 11 attempts to obtain 10 successful runs because one
  device-registration TLS attempt failed with mbedTLS `-0x7280`.
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

## Test B: Prompt and Tool Schema

B0 reuses the D2 batch because it used the current pre-B1 System Prompt, current
Tool schema, the same model, the same fixed Function Calling PCM, and the same
post-call `input_tts` strategy. B1 used the operator-confirmed minimal System
Prompt and minimal `get_magic_number` schema.

| Metric | B0 mean | B0 P50 | B0 P90 | B1 mean | B1 P50 | B1 P90 |
|---|---:|---:|---:|---:|---:|---:|
| Last input -> VAD stop | 423.8 | 337 | 587 | 306.4 | 303 | 323 |
| VAD stop -> Function Call | 1401.8 | 1406 | 1682 | 2144.4 | 1424 | 3370 |
| Last input -> Function Call | 1825.6 | 1743 | 2309 | 2450.8 | 1813 | 3599 |
| Tool output -> first feedback audio | 451.5 | 418 | 470 | 531.3 | 439 | 651 |
| Last input -> first feedback audio | 2280.2 | 2211 | 2703 | 2985.3 | 2219 | 4806 |

All values are milliseconds.

Observed result:

- Function Call correctness: B0 `10/10`, B1 `10/10`.
- Function name correctness: B1 `10/10 get_magic_number`.
- Argument correctness: B1 `10/10 {}`.
- B1 P50 FC decision was effectively unchanged: `1424 ms` versus `1406 ms`.
- B1 tail latency was worse: P90 `3370 ms` versus `1682 ms`.
- B1 mean VAD-stop-to-call increased by `742.6 ms` (`53.0%`).

B0 and B1 ran in different time windows, so server-load jitter is a confounding
factor. The result is sufficient to reject the claim that the minimal Prompt
produced a clear latency improvement, but it is not a tightly time-matched causal
benchmark. A stricter comparison would alternate B0/B1 in smaller blocks.

B1 required 11 attempts to obtain 10 successes. Attempt 2 failed before WebSocket
startup during device registration with mbedTLS error `-0x7280`; it was preserved
and excluded from latency statistics.

Raw B1 successful runs:

| Attempt | Success run | Input->VAD | VAD->FC | Input->FC | Tool->audio | Input->audio | Input->done |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 1 | 278 | 1385 | 1663 | 439 | 2107 | 2335 |
| 3 | 2 | 312 | 2262 | 2574 | 445 | 3023 | 6312 |
| 4 | 3 | 297 | 1401 | 1698 | 390 | 2095 | 3406 |
| 5 | 4 | 389 | 1424 | 1813 | 404 | 2219 | 2369 |
| 6 | 5 | 323 | 4108 | 4431 | 430 | 4862 | 5252 |
| 7 | 6 | 303 | 2942 | 3245 | 651 | 3897 | 7096 |
| 8 | 7 | 314 | 1406 | 1720 | 382 | 2106 | 3426 |
| 9 | 8 | 299 | 959 | 1258 | 513 | 1775 | 2082 |
| 10 | 9 | 320 | 2187 | 2507 | 453 | 2963 | 4318 |
| 11 | 10 | 229 | 3370 | 3599 | 1206 | 4806 | 6945 |

Raw artifacts:

`/home/wte/wheeltec_robot-volc-smoke/experiments/volc_s2s_smoke/artifacts/ab_latency/B1_minimal_prompt_20260812_072256`

## Test C: Seed 2.1 Turbo versus Seed 2.0 Lite

The first model comparison kept the B1 minimal Prompt/Tool schema, fixed PCM,
`input_tts` feedback strategy, and all client-side settings unchanged. Only the
console-selected model changed.

Fixed PCM SHA256:

`c8e0c24793b68b0974de7a00beef3c585514c1c48206dc03f21baeb42c5ddee0`

| Metric | 2.0 Lite B1 mean | Lite P50 | Lite P90 | 2.1 Turbo mean | Turbo P50 | Turbo P90 | Mean change |
|---|---:|---:|---:|---:|---:|---:|---:|
| Last input -> VAD stop | 306.4 | 303 | 323 | 360.6 | 326 | 471 | +54.2 |
| VAD stop -> Function Call | 2144.4 | 1424 | 3370 | 1932.1 | 1918 | 2446 | -212.3 (`-9.9%`) |
| Last input -> Function Call | 2450.8 | 1813 | 3599 | 2292.7 | 2244 | 2952 | -158.1 (`-6.5%`) |
| Function Call -> arguments done | N/A | N/A | N/A | 60.1 | 61 | 89 | N/A |
| Tool output -> first feedback audio | 531.3 | 439 | 651 | 423.3 | 406 | 499 | -108.0 (`-20.3%`) |
| Last input -> first feedback audio | 2985.3 | 2219 | 4806 | 2776.6 | 2720 | 3442 | -208.7 (`-7.0%`) |

All values are milliseconds. A negative change is faster.

Observed result:

- Turbo Function Call correctness: `10/10`.
- Function name correctness: `10/10 get_magic_number`.
- Argument correctness: `10/10 {}`.
- Turbo required 11 attempts for 10 successes. Attempt 8 failed during device
  registration with the same mbedTLS `-0x7280` previously observed in B1.
- Turbo improved the mean and P90, but its FC-decision P50 was `494 ms` slower
  and its end-to-feedback P50 was `501 ms` slower.
- The two batches ran in different time windows. The result supports a Turbo
  tail-latency advantage in these samples, but not a claim that Turbo is always
  faster. A time-matched Lite rerun is still needed as a closing control.

Raw Turbo successful runs:

| Attempt | Success run | Input->VAD | VAD->FC | Input->FC | FC->args | Tool/input_tts->audio | Input->audio | Input->done |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 1 | 327 | 2316 | 2643 | 64 | 499 | 3206 | 3374 |
| 2 | 2 | 283 | 1332 | 1615 | 89 | 394 | 2098 | 2293 |
| 3 | 3 | 303 | 1106 | 1409 | 62 | 412 | 1884 | 4213 |
| 4 | 4 | 326 | 1918 | 2244 | 61 | 414 | 2720 | 2949 |
| 5 | 5 | 325 | 3248 | 3573 | 0 | 580 | 4154 | 7532 |
| 6 | 6 | 344 | 1981 | 2325 | 61 | 371 | 2758 | 3043 |
| 7 | 7 | 506 | 2446 | 2952 | 54 | 436 | 3442 | 6804 |
| 9 | 8 | 310 | 1235 | 1545 | 61 | 395 | 2001 | 2234 |
| 10 | 9 | 471 | 2264 | 2735 | 56 | 326 | 3118 | 4360 |
| 11 | 10 | 411 | 1475 | 1886 | 93 | 406 | 2385 | 2464 |

Raw artifacts:

`/home/wte/wheeltec_robot-volc-smoke/experiments/volc_s2s_smoke/artifacts/ab_latency/C_seed_2_1_turbo_260628_20260812_082231`

## Answers to the requested questions

### A. How fast is the S2S path itself?

With the current Bot and fixed Pure S2S PCM, last input to first response audio was
`2111.6 ms` mean, `1784 ms` P50, and `3033 ms` P90. Server VAD tail contributed
`693.2 ms` mean; the remaining VAD-stop-to-audio black box contributed `1418.4 ms`
mean. A separate ASR-complete event was not available.

### B. How much can Prompt/Tool tuning reduce FC decision time?

No clear reduction was observed. B1 P50 remained almost unchanged (`1424 ms`
versus `1406 ms`) and P90 worsened (`3370 ms` versus `1682 ms`). Under these
samples, simplifying Prompt/Tool text is not the main latency lever; service/model
decision variance dominates.

### C. Which model is fastest for Function Calling?

Not decided yet. Seed 2.1 Turbo has now been measured against the Seed 2.0 Lite
B1 batch. Turbo reduced mean VAD-stop-to-call latency from `2144.4 ms` to
`1932.1 ms` and P90 from `3370 ms` to `2446 ms`, but P50 increased from
`1424 ms` to `1918 ms`. More fixed-model candidates and a closing Lite control
are required before selecting a winner.

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
- Test C currently contains only Lite and Turbo; it is not a complete model
  benchmark.
- B0 and B1 were not interleaved in the same time window.
- Lite and Turbo were not interleaved in the same time window.
- No production bridge or legacy voice module was changed.
