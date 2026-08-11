# Volcengine Embedded Kit low-load WS smoke report

## Scope

Native Jetson Orin Nano / Ubuntu aarch64 validation of official SDK commit
`2c94f96f3aad4094e0e818cbb031149fd4384ead`, with WebSocket enabled and RTC
disabled. This Spike is isolated from the existing Project LINK voice chain.

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

- [NOT TESTED] ARM64 compile
  - Observed: implementation and ARM64-only build gate prepared.
  - Expected: ELF64 AArch64 `volc_ws_smoke` and static WS SDK archive.
  - Error: build has not yet been executed at this report revision.
  - Probable layer: build verification pending.
  - Next action: run `./scripts/build.sh` on Orin.

- [NOT TESTED] WS initialization
- [NOT TESTED] TLS/WSS connect
- [NOT TESTED] authentication
- [NOT TESTED] PCM upload
- [NOT TESTED] server speech detection
- [NOT TESTED] AI audio received
- [NOT TESTED] AI audio playable
- [NOT TESTED] mixed orchestration
- [NOT TESTED] function call received
- [NOT TESTED] function output returned
- [NOT TESTED] final AI response

The online items require valid `VOLC_BOT_ID`, `VOLC_INSTANCE_ID`,
`VOLC_PRODUCT_KEY`, `VOLC_PRODUCT_SECRET`, and `VOLC_DEVICE_NAME`, plus the
appropriate Bot/account console configuration. No credential values are stored
in this report.

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
