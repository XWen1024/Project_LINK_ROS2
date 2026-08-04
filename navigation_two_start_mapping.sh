#!/usr/bin/env bash
set -euo pipefail

WORKSPACE="${PROJECT_LINK_WORKSPACE:-/home/wte/wheeltec_robot}"
BASE_SESSION="${PROJECT_LINK_BASE_TMUX_SESSION:-project_link_c63_base}"
POINT_LIO_SESSION="${PROJECT_LINK_TMUX_SESSION:-project_link_point_lio}"
WAIT_TIMEOUT="${NAVIGATION_TWO_WAIT_TIMEOUT:-60}"
RESTART=0
ATTACH=0

usage() {
  cat <<EOF
Usage: ./navigation_two_start_mapping.sh [--restart] [--attach]

Start C63A base + Point-LIO Phase B live mapping. Nav2 is stopped first so
keyboard teleop can be used without another /cmd_vel controller.
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

if ! command -v tmux >/dev/null 2>&1; then
  echo "tmux is required" >&2
  exit 1
fi

publish_stop() {
  timeout 3 ros2 topic pub --once /cmd_vel geometry_msgs/msg/Twist '{}' >/dev/null 2>&1 || true
  timeout 3 ros2 topic pub --once /cmd_vel_nav geometry_msgs/msg/Twist '{}' >/dev/null 2>&1 || true
}

stop_nav2() {
  publish_stop
  tmux kill-session -t "${PROJECT_LINK_NAV2_TMUX_SESSION:-project_link_point_lio_nav2}" 2>/dev/null || true
  pkill -f 'point_lio_navigation.launch.py' || true
  pkill -f '/nav2_controller/controller_server' || true
  pkill -f '/nav2_smoother/smoother_server' || true
  pkill -f '/nav2_planner/planner_server' || true
  pkill -f '/nav2_behaviors/behavior_server' || true
  pkill -f '/nav2_bt_navigator/bt_navigator' || true
  pkill -f '/nav2_waypoint_follower/waypoint_follower' || true
  pkill -f '/nav2_velocity_smoother/velocity_smoother' || true
  pkill -f 'lifecycle_manager_navigation' || true
}

start_base() {
  if ros2 node list 2>/dev/null | grep -qx '/wheeltec_robot'; then
    echo "[base] Reusing running /wheeltec_robot."
    return
  fi

  if [[ ! -e /dev/wheeltec_controller ]]; then
    echo "C63A device not found: /dev/wheeltec_controller" >&2
    exit 1
  fi

  tmux kill-session -t "$BASE_SESSION" 2>/dev/null || true
  tmux new-session -d -s "$BASE_SESSION" -n base
  tmux send-keys -t "$BASE_SESSION:base" \
    "cd '$WORKSPACE' && source scripts/project_link_env.sh && ros2 launch turn_on_wheeltec_robot base_serial.launch.py" C-m
}

wait_for_topic() {
  local topic="$1"
  echo "[wait] $topic"
  timeout "$WAIT_TIMEOUT" ros2 topic echo --once "$topic" --field header >/dev/null
}

stop_nav2

if [[ "$RESTART" -eq 1 ]]; then
  tmux kill-session -t "$BASE_SESSION" 2>/dev/null || true
  pkill -f 'base_serial.launch.py' || true
  pkill -f 'wheeltec_robot_node' || true
fi

start_base
wait_for_topic /odom

point_lio_args=(--with-2d-map --no-attach)
if [[ "$RESTART" -eq 1 ]]; then
  point_lio_args=(--restart --with-2d-map --no-attach)
fi
./start_point_lio_tmux.sh "${point_lio_args[@]}"

wait_for_topic /odom_lio
wait_for_topic /scan_accumulated
wait_for_topic /map

echo "Navigation Two mapping is ready."
echo "Base tmux:      tmux attach -t $BASE_SESSION"
echo "Mapping tmux:   tmux attach -t $POINT_LIO_SESSION"
echo "Keyboard drive: ./scripts/c63_keyboard_teleop.sh"

if [[ "$ATTACH" -eq 1 ]]; then
  if [[ -n "${TMUX:-}" ]]; then
    tmux switch-client -t "$POINT_LIO_SESSION"
  else
    tmux attach -t "$POINT_LIO_SESSION"
  fi
fi
