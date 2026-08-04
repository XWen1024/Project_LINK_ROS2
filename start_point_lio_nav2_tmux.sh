#!/usr/bin/env bash
set -euo pipefail

SESSION="${PROJECT_LINK_NAV2_TMUX_SESSION:-project_link_point_lio_nav2}"
WORKSPACE="${PROJECT_LINK_WORKSPACE:-/home/wte/wheeltec_robot}"
WAIT_TIMEOUT="${WAIT_FOR_POINT_LIO_NAV2_TIMEOUT:-30}"

ATTACH=1
CLEAN=0
STOP=0

usage() {
  cat <<EOF
Usage: ./start_point_lio_nav2_tmux.sh [options]

Start Nav2 only, reusing an already-running Point-LIO Phase B mapping stack.
This script starts no AMCL, map_server, slam_toolbox, lidar, odometry, or base.
It does not send a navigation goal or a nonzero velocity command.
The C63A base node must already publish /odom for chassis velocity feedback.

Options:
  --restart    Stop the Nav2 tmux session and known Nav2-only processes first.
  --clean      Stop known Nav2-only processes before starting.
  --stop       Stop only the Nav2 tmux session.
  --no-attach  Start without attaching.
  --attach     Attach after starting (default).
  -h, --help   Show this help.

Before sending a 2D Goal Pose, close the keyboard teleop so only Nav2 publishes
/cmd_vel, clear the robot footprint, and keep the physical E-stop ready.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --restart) CLEAN=1; STOP=1 ;;
    --clean) CLEAN=1 ;;
    --stop) STOP=1 ;;
    --no-attach) ATTACH=0 ;;
    --attach) ATTACH=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

kill_session() {
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    tmux kill-session -t "$SESSION"
  fi
}

clean_nav2_processes() {
  echo "[clean] Stopping Point-LIO Nav2-only processes..."
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

if [[ "$STOP" -eq 1 ]]; then
  kill_session
fi
if [[ "$CLEAN" -eq 1 ]]; then
  clean_nav2_processes
fi
if [[ "$STOP" -eq 1 && "$CLEAN" -eq 0 ]]; then
  echo "Stopped tmux session: $SESSION"
  exit 0
fi

if [[ ! -d "$WORKSPACE" ]]; then
  echo "Workspace not found: $WORKSPACE" >&2
  exit 1
fi
if ! command -v tmux >/dev/null 2>&1; then
  echo "tmux is required" >&2
  exit 1
fi
if pgrep -f 'c63_keyboard_teleop\..*\.py' >/dev/null 2>&1; then
  echo "Keyboard teleop is still running and publishes /cmd_vel at 20 Hz." >&2
  echo "Stop its SSH terminal before starting Nav2." >&2
  exit 1
fi
if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "Session already exists: $SESSION"
  if [[ "$ATTACH" -eq 1 ]]; then tmux attach -t "$SESSION"; fi
  exit 0
fi

wait_cmd="wait_for_topic() { local topic=\"\$1\"; echo \"[wait] \${topic}\"; timeout '$WAIT_TIMEOUT' ros2 topic echo --once \"\${topic}\" --field header >/dev/null; }; wait_for_topic /map && wait_for_topic /odom_lio && wait_for_topic /odom && wait_for_topic /scan_accumulated"
nav_cmd="cd '$WORKSPACE' && source scripts/project_link_env.sh && $wait_cmd && ros2 launch wheeltec_nav2 point_lio_navigation.launch.py"
check_cmd="cd '$WORKSPACE' && source scripts/project_link_env.sh && while true; do clear; date; echo 'Nav2 lifecycle:'; for node in controller_server smoother_server planner_server behavior_server bt_navigator waypoint_follower velocity_smoother; do printf '%-24s ' \"\$node\"; timeout 3 ros2 lifecycle get /\"\$node\" 2>/dev/null || true; done; echo; echo 'Costmaps:'; for topic in /local_costmap/costmap /global_costmap/costmap; do echo \"=== \$topic ===\"; timeout -s INT 4 ros2 topic hz \"\$topic\" 2>&1 | grep -E 'average rate|WARNING|does not appear' | tail -n 2 || true; done; echo; echo 'No goal is sent by this script. Stop keyboard teleop before using 2D Goal Pose.'; sleep 5; done"

tmux new-session -d -s "$SESSION" -n nav2
tmux send-keys -t "$SESSION:nav2" "$nav_cmd" C-m
tmux new-window -t "$SESSION" -n check
tmux send-keys -t "$SESSION:check" "$check_cmd" C-m
tmux select-window -t "$SESSION:check"

echo "tmux session is ready: $SESSION"
echo "Attach later with: tmux attach -t $SESSION"
if [[ "$ATTACH" -eq 1 ]]; then
  if [[ -n "${TMUX:-}" ]]; then tmux switch-client -t "$SESSION"; else tmux attach -t "$SESSION"; fi
fi
