#!/usr/bin/env bash
set -euo pipefail

WORKSPACE="${PROJECT_LINK_WORKSPACE:-/home/wte/wheeltec_robot}"
VOICE_ENV="${PROJECT_LINK_VOICE_ENV:-/home/wte/.config/project_link/voice_api.env}"
WAYPOINTS="${PROJECT_LINK_VOICE_WAYPOINTS:-/home/wte/.ros/project_link_voice/waypoints.json}"
VOICE_SESSION="${PROJECT_LINK_VOICE_NAV2_SESSION:-project_link_voice_nav2}"
START_NAVIGATION=1
START_VISUAL=0
ENABLE_MOTION=false
ENABLE_VISUAL_GRASP=false
ENABLE_AUDIO=true
RESTART=0
ATTACH=0
WAKEUP_PORT=auto
AUDIO_INPUT_INDEX=0

usage() {
  cat <<EOF
Usage: ./scripts/start_voice_nav2_stack.sh [options]

Starts Navigation Two plus ASR -> DeepSeek tools -> confirmation -> Nav2 -> optional visual grasp.
Default is safe dry-run: Nav2 may start, but voice sends no navigation goal.

Options:
  --restart                 Restart Navigation Two, voice, and optional visual-grasp sessions.
  --no-navigation           Do not start Navigation Two; require an existing /navigate_to_pose server.
  --with-visual             Start the visual-grasp stack.
  --enable-motion           Permit confirmed named-waypoint Nav2 goals.
  --enable-visual-grasp     Permit TrackAndGrasp after successful Nav2 arrival.
  --no-audio                Disable wakeup/audio; use /voice/text_input.
  --waypoints PATH          Named waypoint JSON.
  --wakeup-port PORT        Wake serial path or auto.
  --audio-input-index N     PyAudio microphone index.
  --attach                  Attach to the voice tmux session.
  -h, --help                Show help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --restart) RESTART=1 ;;
    --no-navigation) START_NAVIGATION=0 ;;
    --with-visual) START_VISUAL=1 ;;
    --enable-motion) ENABLE_MOTION=true ;;
    --enable-visual-grasp) ENABLE_VISUAL_GRASP=true ;;
    --no-audio) ENABLE_AUDIO=false ;;
    --waypoints) WAYPOINTS="$2"; shift ;;
    --wakeup-port) WAKEUP_PORT="$2"; shift ;;
    --audio-input-index) AUDIO_INPUT_INDEX="$2"; shift ;;
    --attach) ATTACH=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

cd "$WORKSPACE"
set +u
source scripts/project_link_env.sh
if [[ -f "$VOICE_ENV" ]]; then source "$VOICE_ENV"; fi
set -u

mkdir -p "$(dirname "$WAYPOINTS")"
[[ -f "$WAYPOINTS" ]] || printf '{}\n' > "$WAYPOINTS"

if [[ "$START_NAVIGATION" -eq 1 ]]; then
  navigation_args=(--no-attach)
  if [[ "$RESTART" -eq 1 ]]; then navigation_args=(--restart --no-attach); fi
  ./navigation_two_start.sh "${navigation_args[@]}"
elif ! timeout 10 ros2 action list 2>/dev/null | grep -qx '/navigate_to_pose'; then
  echo "Nav2 action /navigate_to_pose is unavailable." >&2
  exit 1
fi

if [[ "$START_VISUAL" -eq 1 ]]; then
  visual_args=()
  if [[ "$RESTART" -eq 1 ]]; then visual_args=(--restart); fi
  ./scripts/start_visual_grasp_tmux.sh "${visual_args[@]}"
fi

if tmux has-session -t "$VOICE_SESSION" 2>/dev/null; then
  if [[ "$RESTART" -eq 1 ]]; then tmux kill-session -t "$VOICE_SESSION"; else exit 0; fi
fi

voice_cmd="cd '$WORKSPACE' && source scripts/project_link_env.sh"
if [[ -f "$VOICE_ENV" ]]; then voice_cmd+=" && source '$VOICE_ENV'"; fi
voice_cmd+=" && ros2 launch project_link_voice voice_nav2.launch.py"
voice_cmd+=" enable_motion:=$ENABLE_MOTION enable_audio:=$ENABLE_AUDIO"
voice_cmd+=" enable_visual_grasp:=$ENABLE_VISUAL_GRASP"
voice_cmd+=" waypoints_override_file:='$WAYPOINTS'"
voice_cmd+=" wakeup_serial_port:='$WAKEUP_PORT' audio_input_device_index:=$AUDIO_INPUT_INDEX"
voice_cmd+=" params_file:='$WORKSPACE/src/project_link_voice/config/voice_direct_drive.yaml'"

tmux new-session -d -s "$VOICE_SESSION" -n voice "$voice_cmd"

cat <<EOF
Voice Nav2 stack started.
  Session:       $VOICE_SESSION
  Waypoints:     $WAYPOINTS
  Motion:        $ENABLE_MOTION
  Visual grasp:  $ENABLE_VISUAL_GRASP
  Wake serial:   $WAKEUP_PORT
  Microphone:    $AUDIO_INPUT_INDEX

No goal is sent until a named waypoint task receives explicit voice confirmation.
EOF

if [[ "$ATTACH" -eq 1 ]]; then tmux attach -t "$VOICE_SESSION"; fi
