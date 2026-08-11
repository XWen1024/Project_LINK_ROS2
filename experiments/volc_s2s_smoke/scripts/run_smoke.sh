#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
EXPERIMENT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
BINARY="${EXPERIMENT_DIR}/build/volc_ws_smoke"
ARTIFACT_DIR="${EXPERIMENT_DIR}/artifacts"
ENV_FILE="${EXPERIMENT_DIR}/.env.local"

umask 077
mkdir -p "${ARTIFACT_DIR}"
exec > >(tee "${ARTIFACT_DIR}/smoke.log") 2>&1

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
  echo "Credentials file: ${ENV_FILE} (values redacted)"
fi

required_vars=(
  VOLC_BOT_ID
  VOLC_INSTANCE_ID
  VOLC_PRODUCT_KEY
  VOLC_PRODUCT_SECRET
  VOLC_DEVICE_NAME
)

missing=()
for variable_name in "${required_vars[@]}"; do
  if [[ -z "${!variable_name:-}" ]]; then
    missing+=("${variable_name}")
  fi
done

if ((${#missing[@]} > 0)); then
  echo "ERROR: missing required environment variables:" >&2
  printf '  %s\n' "${missing[@]}" >&2
  echo "Load them from a private file; do not commit credentials." >&2
  exit 2
fi

if ((${#VOLC_PRODUCT_SECRET} < 16)); then
  echo "ERROR: VOLC_PRODUCT_SECRET is unexpectedly short; refusing to call the SDK." >&2
  exit 2
fi

if [[ ! -x "${BINARY}" ]]; then
  echo "ERROR: ${BINARY} does not exist. Run ./scripts/build.sh first." >&2
  exit 2
fi

echo "Credentials: present (values redacted)"
echo "Log: ${ARTIFACT_DIR}/smoke.log"

set +e
stdbuf -oL -eL "${BINARY}" --artifact-dir "${ARTIFACT_DIR}" "$@"
status=$?
set -e

exit "${status}"
