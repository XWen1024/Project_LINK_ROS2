#!/usr/bin/env bash
set -euo pipefail

WORKSPACE="${PROJECT_LINK_WORKSPACE:-/home/wte/wheeltec_robot}"
SESSION="${PROJECT_LINK_UWB_TMUX_SESSION:-project_link_uwb_navigation}"
MODE="shadow"
PARAMS_FILE=""
DEVICE="${PROJECT_LINK_UWB_DEVICE:-/dev/uwb-bu04}"
RESTART=0
ATTACH=0
CONFIRM_MOTION=""

usage() {
  cat <<'EOF'
Usage: ./navigation_two_start_uwb.sh [options]

Start BU04 ingestion and UWB summon/follow on top of an already-healthy
Navigation Two stack. No person-navigation goal is sent by this script.

Options:
  --shadow                  Publish proposed goals only (default)
  --enable-motion           Allow the UWB action server to submit Nav2 goals
  --confirm-motion TOKEN    Required with --enable-motion; TOKEN=UWB-NAV2
  --params FILE             Runtime YAML containing the measured calibration
  --device PATH             Exact stable BU04 device path
  --restart                 Replace an existing UWB tmux session
  --attach                  Attach after startup
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --shadow) MODE="shadow" ;;
    --enable-motion) MODE="live" ;;
    --confirm-motion) shift; CONFIRM_MOTION="${1:-}" ;;
    --params) shift; PARAMS_FILE="${1:-}" ;;
    --device) shift; DEVICE="${1:-}" ;;
    --restart) RESTART=1 ;;
    --attach) ATTACH=1 ;;
    --no-attach) ATTACH=0 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

cd "$WORKSPACE"
set +u
source scripts/project_link_env.sh
source install/setup.bash
set -u

if [[ -z "${PROJECT_LINK_UWB_TAG_ADDRESS:-}" ]]; then
  echo "Set PROJECT_LINK_UWB_TAG_ADDRESS in the local shell; do not commit it." >&2
  exit 1
fi
if [[ ! -e "$DEVICE" ]]; then
  echo "BU04 device does not exist: $DEVICE" >&2
  exit 1
fi
export PROJECT_LINK_UWB_DEVICE="$DEVICE"

for topic in /map /odom /odom_lio /scan_accumulated /local_costmap/costmap /global_costmap/costmap; do
  if ! timeout 5 ros2 topic echo --once "$topic" >/dev/null 2>&1; then
    echo "Navigation Two prerequisite is missing: $topic" >&2
    exit 1
  fi
done
if ! timeout 5 ros2 action list 2>/dev/null | grep -qx '/navigate_to_pose'; then
  echo "Nav2 action /navigate_to_pose is unavailable." >&2
  exit 1
fi

if [[ "$MODE" == "live" ]]; then
  if [[ "$CONFIRM_MOTION" != "UWB-NAV2" ]]; then
    echo "Live mode requires --confirm-motion UWB-NAV2." >&2
    exit 1
  fi
  if [[ -z "$PARAMS_FILE" || ! -f "$PARAMS_FILE" ]]; then
    echo "Live mode requires --params with an operator-approved calibration YAML." >&2
    exit 1
  fi
  if ! grep -Eq 'calibration_status:[[:space:]]*valid([[:space:]]*#.*)?$' "$PARAMS_FILE"; then
    echo "Calibration YAML is not marked valid." >&2
    exit 1
  fi
  if pgrep -f 'c63_keyboard_teleop|rviz_ab_drive.py|ab_drive_server|llm_motion_demo_node' >/dev/null 2>&1; then
    echo "A competing direct-drive or teleop process is running." >&2
    exit 1
  fi
fi

if tmux has-session -t "$SESSION" 2>/dev/null; then
  if [[ "$RESTART" -ne 1 ]]; then
    echo "UWB session already exists: $SESSION (use --restart)." >&2
    exit 1
  fi
  timeout 3 ros2 service call /uwb_navigation/stop std_srvs/srv/Trigger '{}' >/dev/null 2>&1 || true
  tmux kill-session -t "$SESSION"
fi

launch_args=("enable_motion:=$([[ "$MODE" == "live" ]] && echo true || echo false)")
if [[ -n "$PARAMS_FILE" ]]; then
  launch_args+=("params_file:=$PARAMS_FILE")
fi
printf -v launch_command ' %q' "${launch_args[@]}"
tmux new-session -d -s "$SESSION" -n bootstrap
tmux set-environment -t "$SESSION" PROJECT_LINK_UWB_TAG_ADDRESS "$PROJECT_LINK_UWB_TAG_ADDRESS"
tmux set-environment -t "$SESSION" PROJECT_LINK_UWB_DEVICE "$PROJECT_LINK_UWB_DEVICE"
tmux new-window -t "$SESSION" -n uwb
tmux send-keys -t "$SESSION:uwb" \
  "cd '$WORKSPACE' && source scripts/project_link_env.sh && source install/setup.bash && ros2 launch project_link_uwb_navigation uwb_navigation.launch.py$launch_command" C-m
tmux kill-window -t "$SESSION:bootstrap"

for _attempt in $(seq 1 30); do
  if timeout 2 ros2 action list 2>/dev/null | grep -qx '/uwb_navigation/person_navigation' &&
    timeout 2 ros2 topic echo --once /uwb/person_observation >/dev/null 2>&1; then
    echo "UWB Navigation is ready in $MODE mode. No goal has been sent."
    echo "Commands: ./navigation_two_uwb.sh status|summon|follow|stop"
    if [[ "$ATTACH" -eq 1 ]]; then
      if [[ -n "${TMUX:-}" ]]; then tmux switch-client -t "$SESSION"; else tmux attach -t "$SESSION"; fi
    fi
    exit 0
  fi
  sleep 1
done

echo "UWB Navigation did not become ready; inspect: tmux attach -t $SESSION" >&2
exit 1
