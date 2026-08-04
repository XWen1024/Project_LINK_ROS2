#!/usr/bin/env bash
set -euo pipefail

WORKSPACE="${PROJECT_LINK_WORKSPACE:-/home/wte/wheeltec_robot}"
NAV2_SESSION="${PROJECT_LINK_NAV2_TMUX_SESSION:-project_link_point_lio_nav2}"
WAIT_TIMEOUT="${NAVIGATION_TWO_WAIT_TIMEOUT:-60}"
RESTART=0
ATTACH=0

usage() {
  cat <<EOF
Usage: ./navigation_two_start_navigation.sh [--restart] [--attach]

Start the full Navigation Two stack: C63A base, Point-LIO Phase B live mapping,
and Nav2. No goal or nonzero velocity is sent by this script.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
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
set -u

if pgrep -f 'c63_keyboard_teleop\..*\.py' >/dev/null 2>&1; then
  echo "Keyboard teleop is running. Stop it before starting Nav2." >&2
  exit 1
fi

if [[ "$RESTART" -eq 0 ]] && tmux has-session -t "$NAV2_SESSION" 2>/dev/null; then
  if timeout 5 ros2 action list 2>/dev/null | grep -qx '/navigate_to_pose' &&
    timeout 5 ros2 topic echo --once /map --field header >/dev/null 2>&1 &&
    timeout 5 ros2 topic echo --once /odom --field header >/dev/null 2>&1; then
    echo "Navigation Two is already running."
    if [[ "$ATTACH" -eq 1 ]]; then
      if [[ -n "${TMUX:-}" ]]; then tmux switch-client -t "$NAV2_SESSION"; else tmux attach -t "$NAV2_SESSION"; fi
    fi
    exit 0
  fi
  echo "Existing Nav2 tmux is stale; restarting it."
  tmux kill-session -t "$NAV2_SESSION" 2>/dev/null || true
fi

mapping_args=(--no-attach)
nav_args=(--no-attach)
if [[ "$RESTART" -eq 1 ]]; then
  mapping_args=(--restart --no-attach)
  nav_args=(--restart --no-attach)
fi

./navigation_two_start_mapping.sh "${mapping_args[@]}"
./start_point_lio_nav2_tmux.sh "${nav_args[@]}"

wait_for_topic() {
  local topic="$1"
  local deadline=$((SECONDS + WAIT_TIMEOUT))
  echo "[wait] $topic"
  while (( SECONDS < deadline )); do
    if timeout 5 ros2 topic echo --once "$topic" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "Timed out waiting for one message on $topic after ${WAIT_TIMEOUT}s." >&2
  return 1
}

wait_for_topic /local_costmap/costmap
wait_for_topic /global_costmap/costmap

if ! timeout 10 ros2 action list 2>/dev/null | grep -qx '/navigate_to_pose'; then
  echo "Nav2 action /navigate_to_pose is not available." >&2
  exit 1
fi

echo "Navigation Two is ready. No goal has been sent."
echo "Nav2 tmux: tmux attach -t $NAV2_SESSION"

if [[ "$ATTACH" -eq 1 ]]; then
  if [[ -n "${TMUX:-}" ]]; then
    tmux switch-client -t "$NAV2_SESSION"
  else
    tmux attach -t "$NAV2_SESSION"
  fi
fi
