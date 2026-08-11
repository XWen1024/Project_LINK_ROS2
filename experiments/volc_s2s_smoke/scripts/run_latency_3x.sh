#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
EXPERIMENT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
PCM_PATH="${1:-${EXPERIMENT_DIR}/assets/get_magic_number.pcm}"
RUN_ROOT="${2:-${EXPERIMENT_DIR}/artifacts/latency_3x_$(date +%Y%m%d_%H%M%S)}"

if [[ ! -f "${PCM_PATH}" ]]; then
  echo "ERROR: PCM input not found: ${PCM_PATH}" >&2
  exit 2
fi

if [[ -e "${RUN_ROOT}" ]]; then
  echo "ERROR: run output path already exists: ${RUN_ROOT}" >&2
  exit 2
fi
mkdir -p "${RUN_ROOT}"

for run in 1 2 3; do
  run_dir="${RUN_ROOT}/run_${run}"
  echo "===== latency run ${run}/3 ====="
  "${SCRIPT_DIR}/run_smoke.sh" \
    --artifact-dir "${run_dir}" \
    --pcm "${PCM_PATH}" \
    --expect-function-call \
    --response-timeout-sec 90
done

metrics=(
  authentication_registration_ms
  connect_ms
  input_end_to_vad_stop_ms
  vad_stop_to_asr_complete_ms
  vad_stop_to_function_call_ms
  input_end_to_function_call_ms
  function_call_to_args_done_ms
  local_function_output_ms
  function_output_send_ms
  function_output_to_response_created_ms
  response_created_to_first_final_audio_ms
  function_output_to_first_final_audio_ms
  input_end_to_first_ai_audio_ms
  input_end_to_first_final_audio_ms
  first_final_audio_to_audio_done_ms
  first_final_audio_to_response_done_ms
  input_end_to_response_done_ms
)

raw_tsv="${RUN_ROOT}/latency_runs.tsv"
summary_tsv="${RUN_ROOT}/latency_summary.tsv"

{
  printf 'run'
  for metric in "${metrics[@]}"; do
    printf '\t%s' "${metric}"
  done
  printf '\n'

  for run in 1 2 3; do
    log_file="${RUN_ROOT}/run_${run}/smoke.log"
    printf '%s' "${run}"
    for metric in "${metrics[@]}"; do
      value="$(grep -E "^${metric}=" "${log_file}" | tail -n 1 | cut -d= -f2- || true)"
      value="${value%ms}"
      printf '\t%s' "${value:-N/A}"
    done
    printf '\n'
  done
} >"${raw_tsv}"

awk -F '\t' '
  NR == 1 {
    for (column = 2; column <= NF; column++) {
      name[column] = $column
    }
    next
  }
  {
    for (column = 2; column <= NF; column++) {
      if ($column != "N/A" && $column != "") {
        value = $column + 0
        count[column]++
        sum[column] += value
        if (count[column] == 1 || value < min[column]) min[column] = value
        if (count[column] == 1 || value > max[column]) max[column] = value
      }
    }
  }
  END {
    print "metric\tcount\tmean_ms\tmin_ms\tmax_ms"
    for (column = 2; column <= length(name) + 1; column++) {
      if (count[column] > 0) {
        printf "%s\t%d\t%.1f\t%.0f\t%.0f\n", name[column], count[column], sum[column] / count[column], min[column], max[column]
      } else {
        printf "%s\t0\tN/A\tN/A\tN/A\n", name[column]
      }
    }
  }
' "${raw_tsv}" >"${summary_tsv}"

echo "===== raw runs ====="
column -t -s $'\t' "${raw_tsv}" 2>/dev/null || cat "${raw_tsv}"
echo "===== summary ====="
column -t -s $'\t' "${summary_tsv}" 2>/dev/null || cat "${summary_tsv}"
echo "Artifacts: ${RUN_ROOT}"
