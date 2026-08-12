#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
EXPERIMENT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

pcm_path=""
pairs=10
max_attempts=30
run_root=""

usage() {
  cat <<'EOF'
Usage: run_commit_ab.sh --pcm PATH [options]

Alternates fixed-PCM M0 server-VAD and M1 client-commit runs. Odd pairs run
M0/M1; even pairs run M1/M0 to reduce time-order bias.

Options:
  --pairs N          Successful runs per mode (default: 10).
  --max-attempts N   Maximum total attempts (default: 30).
  --run-root PATH    Exact artifact directory (must not exist).
EOF
}

while (($# > 0)); do
  case "$1" in
    --pcm)
      pcm_path="${2:?--pcm requires a value}"
      shift 2
      ;;
    --pairs)
      pairs="${2:?--pairs requires a value}"
      shift 2
      ;;
    --max-attempts)
      max_attempts="${2:?--max-attempts requires a value}"
      shift 2
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

if [[ -z "${pcm_path}" || ! -f "${pcm_path}" ]]; then
  echo "ERROR: --pcm must name an existing PCM file" >&2
  exit 2
fi
if [[ ! "${pairs}" =~ ^[1-9][0-9]*$ ]]; then
  echo "ERROR: --pairs must be a positive integer" >&2
  exit 2
fi
if [[ ! "${max_attempts}" =~ ^[1-9][0-9]*$ ]] || ((max_attempts < pairs * 2)); then
  echo "ERROR: --max-attempts must be at least 2 * --pairs" >&2
  exit 2
fi

pcm_path="$(readlink -f "${pcm_path}")"
if [[ -z "${run_root}" ]]; then
  run_root="${EXPERIMENT_DIR}/artifacts/commit_ab/commit_ab_$(date +%Y%m%d_%H%M%S)"
fi
if [[ -e "${run_root}" ]]; then
  echo "ERROR: run output path already exists: ${run_root}" >&2
  exit 2
fi
mkdir -p "${run_root}"

{
  printf 'test=commit_ab\n'
  printf 'm0=server-vad\n'
  printf 'm1=client-commit\n'
  printf 'feedback_strategy=input-tts\n'
  printf 'pairs=%s\n' "${pairs}"
  printf 'max_attempts=%s\n' "${max_attempts}"
  printf 'pcm_path=%s\n' "${pcm_path}"
  printf 'pcm_sha256=%s\n' "$(sha256sum "${pcm_path}" | awk '{print $1}')"
  printf 'pcm_bytes=%s\n' "$(stat -c '%s' "${pcm_path}")"
  printf 'pcm_format=PCM_S16LE/16000Hz/mono\n'
  printf 'git_branch=%s\n' "$(git -C "${EXPERIMENT_DIR}" branch --show-current 2>/dev/null || true)"
  printf 'git_commit=%s\n' "$(git -C "${EXPERIMENT_DIR}" rev-parse HEAD 2>/dev/null || true)"
  printf 'started_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'percentile_method=nearest-rank\n'
} >"${run_root}/metadata.env"

status_tsv="${run_root}/run_status.tsv"
printf 'attempt\tpair\torder\tmode\tmode_run\texit_code\tlog\n' >"${status_tsv}"

attempt=0
m0_success=0
m1_success=0

run_slot() {
  local pair="$1"
  local order="$2"
  local mode="$3"
  local mode_name mode_run run_dir status
  if [[ "${mode}" == "M0" ]]; then
    mode_name="server-vad"
    mode_run=$((m0_success + 1))
  else
    mode_name="client-commit"
    mode_run=$((m1_success + 1))
  fi

  while ((attempt < max_attempts)); do
    attempt=$((attempt + 1))
    run_dir="${run_root}/attempt_$(printf '%02d' "${attempt}")_${mode}"
    echo "===== pair ${pair}/${pairs} order=${order} ${mode} ${mode_name}; attempt ${attempt}/${max_attempts} ====="
    set +e
    "${SCRIPT_DIR}/run_smoke.sh" \
      --artifact-dir "${run_dir}" \
      --pcm "${pcm_path}" \
      --response-timeout-sec 90 \
      --expect-function-call \
      --feedback-strategy input-tts \
      --input-end "${mode_name}"
    status=$?
    set -e
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "${attempt}" "${pair}" "${order}" "${mode}" \
      "$([[ ${status} -eq 0 ]] && printf '%s' "${mode_run}" || printf 'N/A')" \
      "${status}" "${run_dir}/smoke.log" >>"${status_tsv}"
    if ((status == 0)); then
      if [[ "${mode}" == "M0" ]]; then
        m0_success=$((m0_success + 1))
      else
        m1_success=$((m1_success + 1))
      fi
      return 0
    fi
  done
  return 1
}

for ((pair = 1; pair <= pairs; pair++)); do
  if ((pair % 2 == 1)); then
    run_slot "${pair}" "M0-M1" M0 || break
    run_slot "${pair}" "M0-M1" M1 || break
  else
    run_slot "${pair}" "M1-M0" M1 || break
    run_slot "${pair}" "M1-M0" M0 || break
  fi
done

metrics=(
  input_end_to_vad_stop_ms
  input_end_to_commit_ack_ms
  last_frame_start_to_commit_ack_ms
  last_frame_send_ms
  vad_stop_to_function_call_ms
  input_end_to_function_call_ms
  last_frame_start_to_function_call_ms
  commit_ack_to_function_call_ms
  function_call_to_args_done_ms
  function_output_send_ms
  input_tts_to_first_ai_audio_ms
  input_end_to_first_final_audio_ms
)

raw_tsv="${run_root}/latency_runs.tsv"
{
  printf 'attempt\tpair\torder\tmode\tmode_run\texit_code'
  for metric in "${metrics[@]}"; do
    printf '\t%s' "${metric}"
  done
  printf '\n'
  while IFS=$'\t' read -r row_attempt row_pair row_order row_mode row_mode_run row_exit row_log; do
    [[ "${row_attempt}" == "attempt" ]] && continue
    printf '%s\t%s\t%s\t%s\t%s\t%s' \
      "${row_attempt}" "${row_pair}" "${row_order}" "${row_mode}" "${row_mode_run}" "${row_exit}"
    for metric in "${metrics[@]}"; do
      value="$(grep -E "^${metric}=" "${row_log}" 2>/dev/null | tail -n 1 | cut -d= -f2- || true)"
      value="${value%ms}"
      printf '\t%s' "${value:-N/A}"
    done
    printf '\n'
  done <"${status_tsv}"
} >"${raw_tsv}"

summary_tsv="${run_root}/latency_summary.tsv"
awk -F '\t' '
  function sort_values(group, column, count,    i, j, key) {
    for (i = 2; i <= count; i++) {
      key = values[group, column, i]
      j = i - 1
      while (j >= 1 && values[group, column, j] > key) {
        values[group, column, j + 1] = values[group, column, j]
        j--
      }
      values[group, column, j + 1] = key
    }
  }
  function ceil_number(value) { return value == int(value) ? value : int(value) + 1 }
  NR == 1 {
    for (column = 7; column <= NF; column++) name[column] = $column
    next
  }
  $6 == "0" {
    group = $4
    for (column = 7; column <= NF; column++) {
      if ($column != "N/A" && $column != "") {
        value = $column + 0
        count[group, column]++
        values[group, column, count[group, column]] = value
        sum[group, column] += value
      }
    }
  }
  END {
    print "mode\tmetric\tcount\tmean_ms\tp50_ms\tp90_ms\tmin_ms\tmax_ms"
    split("M0 M1", groups, " ")
    for (g = 1; g <= 2; g++) {
      group = groups[g]
      for (column = 7; column <= NF; column++) {
        n = count[group, column]
        if (n > 0) {
          sort_values(group, column, n)
          p50 = ceil_number(0.50 * n)
          p90 = ceil_number(0.90 * n)
          printf "%s\t%s\t%d\t%.1f\t%.0f\t%.0f\t%.0f\t%.0f\n", \
            group, name[column], n, sum[group, column] / n, \
            values[group, column, p50], values[group, column, p90], \
            values[group, column, 1], values[group, column, n]
        } else {
          printf "%s\t%s\t0\tN/A\tN/A\tN/A\tN/A\tN/A\n", group, name[column]
        }
      }
    }
  }
' "${raw_tsv}" >"${summary_tsv}"

{
  printf 'completed_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'attempts=%s\n' "${attempt}"
  printf 'm0_successful_runs=%s\n' "${m0_success}"
  printf 'm1_successful_runs=%s\n' "${m1_success}"
} >>"${run_root}/metadata.env"

echo "===== raw runs ====="
column -t -s $'\t' "${raw_tsv}" 2>/dev/null || cat "${raw_tsv}"
echo "===== summary ====="
column -t -s $'\t' "${summary_tsv}" 2>/dev/null || cat "${summary_tsv}"
echo "Artifacts: ${run_root}"

if ((m0_success < pairs || m1_success < pairs)); then
  exit 1
fi
