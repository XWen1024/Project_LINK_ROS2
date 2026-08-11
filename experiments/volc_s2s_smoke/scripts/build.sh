#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
EXPERIMENT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
BUILD_DIR="${EXPERIMENT_DIR}/build"

if [[ "$(uname -m)" != "aarch64" && "$(uname -m)" != "arm64" ]]; then
  echo "ERROR: this Spike must be built natively on ARM64; detected $(uname -m)." >&2
  exit 2
fi

for command_name in cmake gcc g++ git pkg-config file readelf; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "ERROR: missing build tool: ${command_name}" >&2
    exit 2
  fi
done

if ! pkg-config --exists zlib; then
  echo "ERROR: native zlib development files are missing." >&2
  echo "Suggested package: sudo apt install zlib1g-dev" >&2
  echo "Reason: the official SDK webclient uses zlib; its bundled archive is macOS arm64." >&2
  exit 2
fi

cmake -S "${EXPERIMENT_DIR}" -B "${BUILD_DIR}" \
  -DCMAKE_BUILD_TYPE=RelWithDebInfo
cmake --build "${BUILD_DIR}" --parallel "$(nproc)"

BINARY="${BUILD_DIR}/volc_ws_smoke"
file "${BINARY}"
if ! readelf -h "${BINARY}" | grep -q 'Machine:.*AArch64'; then
  echo "ERROR: built binary is not native AArch64." >&2
  exit 3
fi

echo "PASS: native ARM64 binary built at ${BINARY}"

