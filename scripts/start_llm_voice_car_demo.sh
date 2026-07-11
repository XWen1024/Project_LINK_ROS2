#!/usr/bin/env bash
set -euo pipefail

WORKSPACE="${PROJECT_LINK_WORKSPACE:-/home/wte/wheeltec_robot}"
VOICE_ENV="${PROJECT_LINK_VOICE_ENV:-/home/wte/.config/project_link/voice_api.env}"
SESSION="${PROJECT_LINK_LLM_DEMO_SESSION:-project_link_llm_voice_car_demo}"
START_BASE=1
RESTART=0
ATTACH=1
SCAN_ONLY=0
ENABLE_AUDIO=true
KEYBOARD_WAKEUP=false
WAKEUP_PORT="${WAKEUP_PORT:-auto}"
WAKEUP_MATCH="${WAKEUP_MATCH:-aiui_event}"
AUDIO_INPUT_INDEX="${AUDIO_INPUT_INDEX:--1}"
LINEAR="${DEMO_LINEAR_MPS:-0.06}"
ANGULAR="${DEMO_ANGULAR_RPS:-0.30}"

usage() {
  cat <<EOF
Usage: ./scripts/start_llm_voice_car_demo.sh [options]

Standalone demo:
  iFlytek wake / keyboard -> mic -> FunVAD -> Whisper -> LLM tool call -> Volcano TTS -> bounded /cmd_vel

Options:
  --restart                 Replace existing tmux session.
  --scan-only               Only list serial/audio/env devices.
  --no-base                 Do not start base_serial.launch.py.
  --no-audio                Disable microphone loop; use /voice_demo/text_input only.
  --keyboard-wakeup         Press Enter before speaking instead of serial wake.
  --wakeup-port PATH|auto   Default: $WAKEUP_PORT
  --wakeup-match TEXT       Default: $WAKEUP_MATCH
  --audio-input-index N     PyAudio input device index. Default: $AUDIO_INPUT_INDEX
  --linear MPS              Default: $LINEAR
  --angular RPS             Default: $ANGULAR
  --voice-env PATH          Default: $VOICE_ENV
  --no-attach               Start tmux session but do not attach.
  -h, --help                Show help.

Text test topic:
  /voice_demo/text_input

Status topic:
  /voice_demo/status
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --restart) RESTART=1 ;;
    --scan-only) SCAN_ONLY=1 ;;
    --no-base) START_BASE=0 ;;
    --no-audio) ENABLE_AUDIO=false ;;
    --keyboard-wakeup) KEYBOARD_WAKEUP=true ;;
    --wakeup-port) WAKEUP_PORT="$2"; shift ;;
    --wakeup-match) WAKEUP_MATCH="$2"; shift ;;
    --audio-input-index) AUDIO_INPUT_INDEX="$2"; shift ;;
    --linear) LINEAR="$2"; shift ;;
    --angular) ANGULAR="$2"; shift ;;
    --voice-env) VOICE_ENV="$2"; shift ;;
    --no-attach) ATTACH=0 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

cd "$WORKSPACE"
source scripts/project_link_env.sh

if [[ -f "$VOICE_ENV" ]]; then
  # shellcheck disable=SC1090
  source "$VOICE_ENV"
else
  echo "Warning: voice env file not found: $VOICE_ENV" >&2
fi

echo "[scan] Voice demo IO devices:"
python3 scripts/scan_voice_demo_io.py || true

if [[ "$SCAN_ONLY" -eq 1 ]]; then
  exit 0
fi

if tmux has-session -t "$SESSION" 2>/dev/null; then
  if [[ "$RESTART" -eq 1 ]]; then
    tmux kill-session -t "$SESSION"
  else
    echo "tmux session '$SESSION' already exists. Use --restart to replace it." >&2
    exit 1
  fi
fi

tmux new-session -d -s "$SESSION" -n voice

voice_cmd="cd '$WORKSPACE' && source scripts/project_link_env.sh"
if [[ -f "$VOICE_ENV" ]]; then
  voice_cmd+=" && source '$VOICE_ENV'"
fi
voice_cmd+=" && ros2 launch project_link_voice llm_motion_demo.launch.py"
voice_cmd+=" enable_audio:=$ENABLE_AUDIO"
voice_cmd+=" keyboard_wakeup:=$KEYBOARD_WAKEUP"
voice_cmd+=" wakeup_serial_port:='$WAKEUP_PORT'"
voice_cmd+=" wakeup_match_text:='$WAKEUP_MATCH'"
voice_cmd+=" audio_input_device_index:=$AUDIO_INPUT_INDEX"
voice_cmd+=" demo_linear_mps:=$LINEAR"
voice_cmd+=" demo_angular_rps:=$ANGULAR"
tmux send-keys -t "$SESSION:voice" "$voice_cmd" C-m

if [[ "$START_BASE" -eq 1 ]]; then
  tmux new-window -t "$SESSION" -n base
  base_cmd="cd '$WORKSPACE' && source scripts/project_link_env.sh && ros2 launch turn_on_wheeltec_robot base_serial.launch.py"
  tmux send-keys -t "$SESSION:base" "$base_cmd" C-m
fi

tmux new-window -t "$SESSION" -n test
test_cmd="cd '$WORKSPACE' && source scripts/project_link_env.sh && echo 'Text test examples:' && echo \"ros2 topic pub --once /voice_demo/text_input std_msgs/msg/String \\\"data: '请你往前走两步'\\\"\" && echo \"ros2 topic pub --once /voice_demo/text_input std_msgs/msg/String \\\"data: '帮我转个圈'\\\"\" && echo \"ros2 topic pub --once /voice_demo/text_input std_msgs/msg/String \\\"data: '停止'\\\"\" && bash"
tmux send-keys -t "$SESSION:test" "$test_cmd" C-m

cat <<EOF
Started LLM voice car demo tmux session: $SESSION

Attach:
  tmux attach -t $SESSION

Text tests:
  ros2 topic pub --once /voice_demo/text_input std_msgs/msg/String "data: '请你往前走两步'"
  ros2 topic pub --once /voice_demo/text_input std_msgs/msg/String "data: '请你后退一点'"
  ros2 topic pub --once /voice_demo/text_input std_msgs/msg/String "data: '帮我转个圈'"
  ros2 topic pub --once /voice_demo/text_input std_msgs/msg/String "data: '停止'"

Manual overrides if auto scan picked the wrong device:
  bash scripts/start_llm_voice_car_demo.sh --restart --wakeup-port /dev/ttyUSB0 --audio-input-index 2

Keep a hand on the physical E-stop. This demo publishes bounded /cmd_vel directly.
EOF

if [[ "$ATTACH" -eq 1 ]]; then
  tmux attach -t "$SESSION"
fi
