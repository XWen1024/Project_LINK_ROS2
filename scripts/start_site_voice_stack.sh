#!/usr/bin/env bash
set -euo pipefail

WORKSPACE="${PROJECT_LINK_WORKSPACE:-/home/wte/wheeltec_robot}"
VOICE_ENV="${PROJECT_LINK_VOICE_ENV:-/home/wte/.config/project_link/voice_api.env}"
WAYPOINTS="${PROJECT_LINK_VOICE_WAYPOINTS:-/home/wte/.ros/project_link_voice/waypoints.json}"
VOICE_SESSION="${PROJECT_LINK_VOICE_SESSION:-project_link_voice}"
START_SLAM=1
START_VISUAL=0
ENABLE_MOTION=false
ENABLE_VISUAL_GRASP=false
ENABLE_AUDIO=true
PURE_TEST_MODE=auto
RESTART=0
ATTACH=0

usage() {
  cat <<EOF
Usage: ./scripts/start_site_voice_stack.sh [options]

Starts the site voice stack for ASR -> LLM tools -> guarded direct drive -> optional visual grasp.
Default is safe dry-run: no motion and no arm action.

Options:
  --restart                 Restart tmux sessions for SLAM, voice, and optional visual grasp.
  --no-slam                 Do not start the SLAM tmux stack; assume it is already running.
  --with-visual             Start the visual grasp tmux node.
  --enable-motion           Allow ab_drive_server to accept confirmed drive goals.
  --enable-visual-grasp     Allow voice to call /visual_grasp after successful drive.
  --no-audio                Disable serial wakeup/audio loop; use /voice/text_input only.
  --pure-test               Force voice pure test mode.
  --waypoints PATH          Waypoint override JSON. Default: $WAYPOINTS
  --voice-env PATH          API env file. Default: $VOICE_ENV
  --attach                  Attach to the voice tmux session after start.
  -h, --help                Show help.

Examples:
  ./scripts/start_site_voice_stack.sh
  ./scripts/start_site_voice_stack.sh --enable-motion
  ./scripts/start_site_voice_stack.sh --with-visual --enable-motion --enable-visual-grasp
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --restart) RESTART=1 ;;
    --no-slam) START_SLAM=0 ;;
    --with-visual) START_VISUAL=1 ;;
    --enable-motion) ENABLE_MOTION=true ;;
    --enable-visual-grasp) ENABLE_VISUAL_GRASP=true ;;
    --no-audio) ENABLE_AUDIO=false ;;
    --pure-test) PURE_TEST_MODE=on ;;
    --waypoints) WAYPOINTS="$2"; shift ;;
    --voice-env) VOICE_ENV="$2"; shift ;;
    --attach) ATTACH=1 ;;
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
  echo "Warning: voice API env file not found: $VOICE_ENV" >&2
  echo "LLM and Volcano TTS will run only if their env vars are already exported." >&2
fi

mkdir -p "$(dirname "$WAYPOINTS")"
if [[ ! -f "$WAYPOINTS" ]]; then
  printf '{}\n' > "$WAYPOINTS"
fi

if [[ "$START_SLAM" -eq 1 ]]; then
  slam_args=(--no-attach)
  if [[ "$RESTART" -eq 1 ]]; then
    slam_args=(--restart --no-attach)
  fi
  echo "[1/4] Starting SLAM stack..."
  ./start_slam_tmux.sh "${slam_args[@]}"
else
  echo "[1/4] Skipping SLAM start."
fi

if [[ "$START_VISUAL" -eq 1 ]]; then
  echo "[2/4] Starting visual grasp stack..."
  visual_args=()
  if [[ "$RESTART" -eq 1 ]]; then
    visual_args=(--restart)
  fi
  ./scripts/start_visual_grasp_tmux.sh "${visual_args[@]}"
else
  echo "[2/4] Visual grasp stack not started."
fi

if tmux has-session -t "$VOICE_SESSION" 2>/dev/null; then
  if [[ "$RESTART" -eq 1 ]]; then
    tmux kill-session -t "$VOICE_SESSION"
  else
    echo "Voice tmux session already exists: $VOICE_SESSION"
    echo "Use --restart to replace it, or attach with: tmux attach -t $VOICE_SESSION"
    exit 0
  fi
fi

voice_cmd="cd '$WORKSPACE' && source scripts/project_link_env.sh"
if [[ -f "$VOICE_ENV" ]]; then
  voice_cmd+=" && source '$VOICE_ENV'"
fi
voice_cmd+=" && ros2 launch project_link_voice voice_direct_drive.launch.py"
voice_cmd+=" enable_motion:=$ENABLE_MOTION"
voice_cmd+=" enable_audio:=$ENABLE_AUDIO"
voice_cmd+=" enable_visual_grasp:=$ENABLE_VISUAL_GRASP"
voice_cmd+=" pure_test_mode:=$PURE_TEST_MODE"
voice_cmd+=" waypoints_override_file:='$WAYPOINTS'"
voice_cmd+=" params_file:='$WORKSPACE/src/project_link_voice/config/voice_direct_drive.yaml'"

echo "[3/4] Starting voice stack in tmux session '$VOICE_SESSION'..."
tmux new-session -d -s "$VOICE_SESSION" -n voice "$voice_cmd"

cat <<EOF

[4/4] Site stack started.
  SLAM tmux:          ${PROJECT_LINK_TMUX_SESSION:-project_link_slam}
  Voice tmux:         $VOICE_SESSION
  Visual tmux:        project_link_visual_grasp $(if [[ "$START_VISUAL" -eq 1 ]]; then echo "(started)"; else echo "(not started)"; fi)
  Waypoints JSON:     $WAYPOINTS
  Motion enabled:     $ENABLE_MOTION
  Visual grasp:       $ENABLE_VISUAL_GRASP

Useful checks:
  ros2 topic echo --once /voice/status
  ros2 topic pub --once /voice/text_input std_msgs/msg/String "data: '去客厅'"
  ros2 action list | grep -E 'voice/drive_to_point|visual_grasp'

EOF

if [[ "$ATTACH" -eq 1 ]]; then
  tmux attach -t "$VOICE_SESSION"
fi
