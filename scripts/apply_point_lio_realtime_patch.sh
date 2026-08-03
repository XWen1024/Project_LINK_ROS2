#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
POINT_LIO_SOURCE="${POINT_LIO_SOURCE:-/home/wte/point_lio_ws/src/point_lio}"
PATCH_FILE="${POINT_LIO_REALTIME_PATCH:-$REPO_ROOT/patches/point_lio/0001-bound-realtime-queues.patch}"
CHECK_ONLY=0

if [[ "${1:-}" == "--check" ]]; then
  CHECK_ONLY=1
elif [[ $# -gt 0 ]]; then
  echo "Usage: $0 [--check]" >&2
  exit 2
fi

if [[ ! -d "$POINT_LIO_SOURCE/.git" ]]; then
  echo "Point-LIO Git checkout not found: $POINT_LIO_SOURCE" >&2
  exit 1
fi

if [[ ! -f "$PATCH_FILE" ]]; then
  echo "Patch file not found: $PATCH_FILE" >&2
  exit 1
fi

if git -C "$POINT_LIO_SOURCE" apply --ignore-whitespace --reverse --check "$PATCH_FILE" >/dev/null 2>&1; then
  echo "Point-LIO real-time patch is already applied."
  exit 0
fi

if ! git -C "$POINT_LIO_SOURCE" apply --ignore-whitespace --check "$PATCH_FILE"; then
  echo "Patch does not apply cleanly. No files were changed." >&2
  echo "Inspect only src/laserMapping.cpp; do not reset the existing dirty checkout." >&2
  exit 1
fi

if [[ "$CHECK_ONLY" -eq 1 ]]; then
  echo "Point-LIO real-time patch check passed."
  exit 0
fi

git -C "$POINT_LIO_SOURCE" apply --ignore-whitespace "$PATCH_FILE"
echo "Applied Point-LIO real-time patch to: $POINT_LIO_SOURCE/src/laserMapping.cpp"
echo "Existing unrelated Point-LIO changes were preserved."
git -C "$POINT_LIO_SOURCE" diff --stat -- src/laserMapping.cpp
