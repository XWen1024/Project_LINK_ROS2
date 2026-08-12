# Volcengine fixed-PCM client Commit A/B report

Date: 2026-08-12

Branch: `spike/volc-s2s-ws-smoke`

Test commit: `69131760b11bd897b09e173b1fcd910ed3d85cc0`

SDK commit: `2c94f96f3aad4094e0e818cbb031149fd4384ead`

Platform: Jetson Orin Nano / Ubuntu aarch64

Model observed in `session.created`: `doubao-seed-2-1-turbo-260628`

## Question

Does setting `volc_audio_frame_info_t.commit=true` on the final fixed-PCM frame
reduce the latency to `input_audio_buffer.committed` and Function Calling?

The official low-load WebSocket source at this commit performs this sequence
inside `__ws_send_audio()` when `commit=true`:

```text
input_audio_buffer.append
input_audio_buffer.commit
response.create
```

## Conditions

- M0: final PCM frame uses `commit=false`; server VAD owns endpoint and commit.
- M1: final PCM frame uses `commit=true`; SDK sends client commit and
  `response.create`.
- Input: `assets/get_magic_number.pcm`
- Input SHA-256:
  `c8e0c24793b68b0974de7a00beef3c585514c1c48206dc03f21baeb42c5ddee0`
- Input bytes: `86,780`
- Format: PCM S16LE / 16 kHz / mono
- Cadence: 100 ms
- Tool: `get_magic_number`, arguments `{}`, output `{"number":42}`
- Post-tool feedback: official WebSocket `input_tts`; this keeps second-LLM
  latency out of the commit comparison.
- Each run waits for `session.created` and initial Bot audio to become quiet
  before sending the PCM.
- Ten successful runs per mode, paired and alternated as M0/M1 then M1/M0 to
  reduce time-order bias.
- Percentiles use nearest-rank.

One preliminary sanity pair before session-settle isolation was invalid because
the PCM overlapped Bot initialization audio. It is not included. The clean
one-pair sanity suggested a large FC benefit, but the formal 10+10 result did
not reproduce it; the formal batch is the source of truth.

## Result

| Metric | M0 mean | M0 P50 | M0 P90 | M0 min/max | M1 mean | M1 P50 | M1 P90 | M1 min/max | M1 mean delta |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Last frame -> committed | 1824.3 | 1449 | 2763 | 659 / 2977 | 1083.4 | 434 | 1243 | 236 / 5241 | **-740.9** |
| Last frame -> Function Call | 3481.7 | 3282 | 4832 | 1737 / 4902 | 3470.7 | 3499 | 5241 | 1442 / 5927 | **-11.0** |
| committed -> Function Call | 1657.4 | 1577 | 2550 | 0 / 3453 | 2387.3 | 2500 | 3353 | 0 / 4863 | **+729.9** |
| Tool `input_tts` -> first audio | 516.4 | 429 | 634 | 388 / 716 | 530.6 | 552 | 617 | 364 / 727 | +14.2 |
| Last frame -> first feedback audio | 4115.3 | 3777 | 5348 | 2200 / 5619 | 4036.2 | 3978 | 5859 | 1871 / 6542 | -79.1 |

All values are milliseconds. Negative delta means M1 was faster.

## Interpretation

Client commit materially advances the observable `committed` event:

```text
mean improvement: 740.9 ms (40.6%)
P50 improvement: 1015 ms (70.0%)
```

However, it did **not** produce a stable Function Calling improvement:

```text
M0 last frame -> FC mean: 3481.7 ms
M1 last frame -> FC mean: 3470.7 ms
difference: 11.0 ms (0.3%)
```

The time saved before `committed` reappeared after `committed`: M1
`committed -> FC` was 729.9 ms slower on average. Under the current Turbo model,
Bot configuration, and service conditions, the server does not consistently
start or finish the FC decision earlier merely because the client commit event
arrives earlier.

The paired FC deltas (M1 minus M0) were:

```text
+858, -713, +217, +979, +339,
-1328, -2330, -787, +2950, -295 ms
```

Six of ten pairs favored M1, four favored M0, but variance was several seconds
and the aggregate mean was effectively equal. The one-pair sanity gain was
therefore a favorable cloud-latency sample, not a repeatable causal result.

## Decision

- **Do not migrate client commit into the live microphone path as a latency fix
  yet.** This formal test does not show stable improvement to FC or first
  feedback.
- Keep the explicit client-commit option in the spike for future model/service
  comparisons.
- The proven larger optimization remains direct `input_tts` after tool success;
  it avoids the second LLM and previously reduced tool-output-to-audio mean from
  `2138.2 ms` to `451.5 ms`.
- If client commit is revisited, test it on a persistent WSS session and with a
  true end-of-speech signal. Fixed-file last-frame commit is protocol-valid but
  its timestamp is not equivalent to a live microphone knowing the user has
  stopped speaking.

## Failed attempt

There were 21 attempts for 20 successful samples. Attempt 20 (M0) completed
device registration but timed out waiting for WebSocket connection and later
disconnected. It was retained and excluded from latency statistics. Attempt 21
repeated M0 successfully.

## Raw data

Orin:

```text
/home/wte/wheeltec_robot-volc-smoke/experiments/volc_s2s_smoke/
  artifacts/commit_ab/formal_turbo_20260812/
```

The directory contains:

- `metadata.env`
- `run_status.tsv`
- `latency_runs.tsv`
- `latency_summary.tsv`
- every attempt's `smoke.log`, `function_calls.jsonl`, response PCM and WAV

Generated artifacts remain ignored and are not committed.
