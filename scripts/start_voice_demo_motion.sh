#!/usr/bin/env bash
set -euo pipefail

WORKSPACE="${PROJECT_LINK_WORKSPACE:-/home/wte/wheeltec_robot}"
SESSION="${PROJECT_LINK_DEMO_SESSION:-project_link_voice_demo}"
START_BASE=1
RESTART=0
ATTACH=1
ENABLE_AUDIO=true
KEYBOARD_WAKEUP=true
WAKEUP_PORT="${WAKEUP_PORT:-/dev/ttyUSB0}"
WAKEUP_MATCH="${WAKEUP_MATCH:-aiui_event}"
LINEAR="${DEMO_LINEAR_MPS:-0.06}"
ANGULAR="${DEMO_ANGULAR_RPS:-0.30}"

usage() {
  cat <<EOF
Usage: ./scripts/start_voice_demo_motion.sh [options]

Starts a no-map voice demo:
  voice/text -> bounded /cmd_vel

Default behavior:
  - starts base_serial.launch.py
  - starts voice_demo_motion.launch.py
  - uses keyboard wakeup, so press Enter before speaking
  - accepts text tests on /voice/text_input

Options:
  --restart              Replace existing tmux session.
  --no-base              Do not start base_serial.launch.py.
  --no-audio             Disable microphone/audio loop; use /voice/text_input only.
  --serial-wakeup        Use external wake serial instead of keyboard Enter.
  --wakeup-port PATH     Default: $WAKEUP_PORT
  --wakeup-match TEXT    Default: $WAKEUP_MATCH
  --linear MPS           Default: $LINEAR
  --angular RPS          Default: $ANGULAR
  --no-attach            Start tmux session but do not attach.
  -h, --help             Show help.

Voice/text commands:
  前进 / 走两步
  后退 / 退两步
  左转
  右转
  转个圈 / 转一圈
  停止 / 取消
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --restart) RESTART=1 ;;
    --no-base) START_BASE=0 ;;
    --no-audio) ENABLE_AUDIO=false ;;
    --serial-wakeup) KEYBOARD_WAKEUP=false ;;
    --wakeup-port) WAKEUP_PORT="$2"; shift ;;
    --wakeup-match) WAKEUP_MATCH="$2"; shift ;;
    --linear) LINEAR="$2"; shift ;;
    --angular) ANGULAR="$2"; shift ;;
    --no-attach) ATTACH=0 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

cd "$WORKSPACE"
source scripts/project_link_env.sh

if tmux has-session -t "$SESSION" 2>/dev/null; then
  if [[ "$RESTART" -eq 1 ]]; then
    tmux kill-session -t "$SESSION"
  else
    echo "tmux session '$SESSION' already exists. Use --restart to replace it." >&2
    exit 1
  fi
fi

tmux new-session -d -s "$SESSION" -n voice

voice_cmd="cd '$WORKSPACE' && source scripts/project_link_env.sh && ros2 launch project_link_voice voice_demo_motion.launch.py"
voice_cmd+=" enable_audio:=$ENABLE_AUDIO"
voice_cmd+=" keyboard_wakeup:=$KEYBOARD_WAKEUP"
voice_cmd+=" wakeup_serial_port:='$WAKEUP_PORT'"
voice_cmd+=" wakeup_match_text:='$WAKEUP_MATCH'"
voice_cmd+=" demo_linear_mps:=$LINEAR"
voice_cmd+=" demo_angular_rps:=$ANGULAR"
tmux send-keys -t "$SESSION:voice" "$voice_cmd" C-m

if [[ "$START_BASE" -eq 1 ]]; then
  tmux new-window -t "$SESSION" -n base
  base_cmd="cd '$WORKSPACE' && source scripts/project_link_env.sh && ros2 launch turn_on_wheeltec_robot base_serial.launch.py"
  tmux send-keys -t "$SESSION:base" "$base_cmd" C-m
fi

tmux new-window -t "$SESSION" -n test
test_cmd="cd '$WORKSPACE' && source scripts/project_link_env.sh && echo 'Text test examples:' && echo \"ros2 topic pub --once /voice/text_input std_msgs/msg/String \\\"data: '前进'\\\"\" && echo \"ros2 topic pub --once /voice/text_input std_msgs/msg/String \\\"data: '转个圈'\\\"\" && echo \"ros2 topic pub --once /voice/text_input std_msgs/msg/String \\\"data: '停止'\\\"\" && bash"
tmux send-keys -t "$SESSION:test" "$test_cmd" C-m

cat <<EOF
Started voice demo tmux session: $SESSION

Attach:
  tmux attach -t $SESSION

Try text commands:
  ros2 topic pub --once /voice/text_input std_msgs/msg/String "data: '前进'"
  ros2 topic pub --once /voice/text_input std_msgs/msg/String "data: '后退'"
  ros2 topic pub --once /voice/text_input std_msgs/msg/String "data: '左转'"
  ros2 topic pub --once /voice/text_input std_msgs/msg/String "data: '转个圈'"
  ros2 topic pub --once /voice/text_input std_msgs/msg/String "data: '停止'"

Physical E-stop still matters. Demo motion publishes short bounded /cmd_vel commands.
EOF

if [[ "$ATTACH" -eq 1 ]]; then
  tmux attach -t "$SESSION"
fi
