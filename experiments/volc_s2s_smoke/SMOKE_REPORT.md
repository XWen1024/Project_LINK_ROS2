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
  - Next action: run credentialed WebSocket initialization and TLS connection.

Additional binary checks:

```text
Class: ELF64
Machine: AArch64
RTC_SYMBOLS_ABSENT
X86_RTC_REFERENCE_ABSENT
```

`ldd` resolves only native AArch64 glibc/loader at runtime; mbedTLS is linked
from the native static build, and no RTC library is present.

- [NOT TESTED] WS initialization
  - Observed: the credential gate stopped before `volc_create`.
  - Expected: SDK creation followed by `VOLC_MODE_WS` start.
  - Error: all five required variables were absent from the non-interactive
    Orin SSH process used for the test; no values were guessed or searched for.
  - Probable layer: runtime credential provisioning.
  - Next action: load the private environment in the test shell and rerun.

- [NOT TESTED] TLS/WSS connect
  - Blocked before network initialization by the credential gate.

- [NOT TESTED] authentication
  - Blocked because the official device registration fields were not provided
    to the test process.

- [NOT TESTED] PCM upload
- [NOT TESTED] server speech detection
- [NOT TESTED] AI audio received
- [NOT TESTED] AI audio playable
  - These require a successful authenticated WSS session first.

- [NOT TESTED] mixed orchestration
- [NOT TESTED] function call received
- [NOT TESTED] function output returned
- [NOT TESTED] final AI response
  - These additionally require the account/Bot Function Calling configuration
    and a successful PCM-to-S2S-to-AI-audio baseline.

The online items require valid `VOLC_BOT_ID`, `VOLC_INSTANCE_ID`,
`VOLC_PRODUCT_KEY`, `VOLC_PRODUCT_SECRET`, and `VOLC_DEVICE_NAME`, plus the
appropriate Bot/account console configuration. No credential values are stored
in this report.

## Current blocker

The Orin build is complete, but the non-interactive test shell does not contain
the five required SDK variables. Per the Spike safety rules, testing stopped at
the explicit credential gate. No shell profiles or unrelated private files were
searched, and no credential values were printed.

The credential-gate run exited with code `2` and saved its redacted diagnostic
to `artifacts/smoke.log`.

## First credentialed runs

- Connection-only WSS test passed: device registration completed in `373 ms`,
  WebSocket connected in `2422 ms`, and shutdown was clean.
- The first PCM run reached the service, received `session.created`, server VAD
  LISTENING/THINKING states, and `54,976` bytes of PCM response audio.
- That run stopped at the final commit because the CLI initially treated the
  SDK's positive WebSocket byte-count return (`69`) as an error. Official
  low-load code returns a positive send length from the commit-triggered
  `response.create`; only negative values indicate failure. The CLI was corrected
  without patching the official SDK, and the complete S2S run must be repeated.

## Timing evidence

No online timing samples are available yet. The executable records T0 through
T7 with `CLOCK_MONOTONIC` and prints `N/A` for missing events.

## Artifact locations

```text
artifacts/smoke.log
artifacts/response.pcm
artifacts/response.wav
artifacts/function_calls.jsonl
```

Runtime artifacts are ignored by Git except for `artifacts/.gitkeep`.
