#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
EXPERIMENT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

label=""
pcm_path=""
runs=10
expect_function_call=false
run_root=""

usage() {
  cat <<'EOF'
Usage: run_latency_batch.sh --label LABEL --pcm PATH [options]

Options:
  --runs N                 Number of identical runs (default: 10).
  --expect-function-call   Require the get_magic_number round trip.
  --run-root PATH          Exact artifact directory (must not exist).
EOF
}

while (($# > 0)); do
  case "$1" in
    --label)
      label="${2:?--label requires a value}"
      shift 2
      ;;
    --pcm)
      pcm_path="${2:?--pcm requires a value}"
      shift 2
      ;;
    --runs)
      runs="${2:?--runs requires a value}"
      shift 2
      ;;
    --expect-function-call)
      expect_function_call=true
      shift
      ;;
    --run-root)
      run_root="${2:?--run-root requires a value}"
      shift 2
      ;;
    --help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "${label}" || -z "${pcm_path}" ]]; then
  echo "ERROR: --label and --pcm are required" >&2
  usage >&2
  exit 2
fi
if [[ ! "${runs}" =~ ^[1-9][0-9]*$ ]]; then
  echo "ERROR: --runs must be a positive integer" >&2
  exit 2
fi
if [[ ! -f "${pcm_path}" ]]; then
  echo "ERROR: PCM input not found: ${pcm_path}" >&2
  exit 2
fi

pcm_path="$(readlink -f "${pcm_path}")"
if [[ -z "${run_root}" ]]; then
  run_root="${EXPERIMENT_DIR}/artifacts/ab_latency/${label}_$(date +%Y%m%d_%H%M%S)"
fi
if [[ -e "${run_root}" ]]; then
  echo "ERROR: run output path already exists: ${run_root}" >&2
  exit 2
fi
mkdir -p "${run_root}"

pcm_sha256="$(sha256sum "${pcm_path}" | awk '{print $1}')"
pcm_bytes="$(stat -c '%s' "${pcm_path}")"
git_branch="$(git -C "${EXPERIMENT_DIR}" branch --show-current 2>/dev/null || true)"
git_commit="$(git -C "${EXPERIMENT_DIR}" rev-parse HEAD 2>/dev/null || true)"

{
  printf 'label=%s\n' "${label}"
  printf 'runs=%s\n' "${runs}"
  printf 'expect_function_call=%s\n' "${expect_function_call}"
  printf 'pcm_path=%s\n' "${pcm_path}"
  printf 'pcm_sha256=%s\n' "${pcm_sha256}"
  printf 'pcm_bytes=%s\n' "${pcm_bytes}"
  printf 'pcm_format=PCM_S16LE/16000Hz/mono\n'
  printf 'git_branch=%s\n' "${git_branch}"
  printf 'git_commit=%s\n' "${git_commit}"
  printf 'started_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'percentile_method=nearest-rank\n'
} >"${run_root}/metadata.env"

run_status_tsv="${run_root}/run_status.tsv"
printf 'run\texit_code\tlog\n' >"${run_status_tsv}"

for ((run = 1; run <= runs; run++)); do
  run_dir="${run_root}/run_$(printf '%02d' "${run}")"
  echo "===== ${label} run ${run}/${runs} ====="
  smoke_args=(
    --artifact-dir "${run_dir}"
    --pcm "${pcm_path}"
    --response-timeout-sec 90
  )
  if [[ "${expect_function_call}" == true ]]; then
    smoke_args+=(--expect-function-call)
  fi

  set +e
  "${SCRIPT_DIR}/run_smoke.sh" "${smoke_args[@]}"
  status=$?
  set -e
  printf '%s\t%s\t%s\n' "${run}" "${status}" "${run_dir}/smoke.log" >>"${run_status_tsv}"
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
  response_created_to_first_ai_audio_ms
  response_created_to_first_final_audio_ms
  function_output_to_first_final_audio_ms
  input_end_to_first_ai_audio_ms
  input_end_to_first_final_audio_ms
  first_final_audio_to_audio_done_ms
  first_final_audio_to_response_done_ms
  input_end_to_response_done_ms
)

raw_tsv="${run_root}/latency_runs.tsv"
summary_tsv="${run_root}/latency_summary.tsv"

{
  printf 'run\texit_code'
  for metric in "${metrics[@]}"; do
    printf '\t%s' "${metric}"
  done
  printf '\n'

  for ((run = 1; run <= runs; run++)); do
    run_name="run_$(printf '%02d' "${run}")"
    log_file="${run_root}/${run_name}/smoke.log"
    exit_code="$(awk -F '\t' -v wanted="${run}" '$1 == wanted {print $2}' "${run_status_tsv}")"
    printf '%s\t%s' "${run}" "${exit_code:-N/A}"
    for metric in "${metrics[@]}"; do
      value="$(grep -E "^${metric}=" "${log_file}" 2>/dev/null | tail -n 1 | cut -d= -f2- || true)"
      value="${value%ms}"
      printf '\t%s' "${value:-N/A}"
    done
    printf '\n'
  done
} >"${raw_tsv}"

awk -F '\t' '
  function sort_values(column, count,    i, j, key) {
    for (i = 2; i <= count; i++) {
      key = values[column, i]
      j = i - 1
      while (j >= 1 && values[column, j] > key) {
        values[column, j + 1] = values[column, j]
        j--
      }
      values[column, j + 1] = key
    }
  }
  function ceil_number(value) {
    return value == int(value) ? value : int(value) + 1
  }
  NR == 1 {
    for (column = 3; column <= NF; column++) name[column] = $column
    next
  }
  {
    for (column = 3; column <= NF; column++) {
      if ($column != "N/A" && $column != "") {
        value = $column + 0
        count[column]++
        values[column, count[column]] = value
        sum[column] += value
      }
    }
  }
  END {
    print "metric\tcount\tmean_ms\tp50_ms\tp90_ms\tmin_ms\tmax_ms"
    for (column = 3; column <= NF; column++) {
      if (count[column] > 0) {
        sort_values(column, count[column])
        p50_index = ceil_number(0.50 * count[column])
        p90_index = ceil_number(0.90 * count[column])
        printf "%s\t%d\t%.1f\t%.0f\t%.0f\t%.0f\t%.0f\n", \
          name[column], count[column], sum[column] / count[column], \
          values[column, p50_index], values[column, p90_index], \
          values[column, 1], values[column, count[column]]
      } else {
        printf "%s\t0\tN/A\tN/A\tN/A\tN/A\tN/A\n", name[column]
      }
    }
  }
' "${raw_tsv}" >"${summary_tsv}"

printf 'completed_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"${run_root}/metadata.env"

echo "===== raw runs ====="
column -t -s $'\t' "${raw_tsv}" 2>/dev/null || cat "${raw_tsv}"
echo "===== summary ====="
column -t -s $'\t' "${summary_tsv}" 2>/dev/null || cat "${summary_tsv}"
echo "Artifacts: ${run_root}"
