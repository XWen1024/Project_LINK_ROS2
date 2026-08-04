#!/usr/bin/env bash
set -euo pipefail

WORKSPACE="${PROJECT_LINK_WORKSPACE:-/home/wte/wheeltec_robot}"
SESSION="${NAVIGATION_TWO_STATUS_SESSION:-project_link_navigation_two_status}"
ATTACH=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --attach) ATTACH=1 ;;
    --no-attach) ATTACH=0 ;;
    -h|--help) echo "Usage: ./navigation_two_status.sh [--attach|--no-attach]"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

cd "$WORKSPACE"
set +u
source scripts/project_link_env.sh
set -u

tmux kill-session -t "$SESSION" 2>/dev/null || true
tmux new-session -d -s "$SESSION" -n status

status_cmd="cd '$WORKSPACE' && source scripts/project_link_env.sh && while true; do clear; date; echo; echo 'tmux sessions:'; tmux ls 2>/dev/null || true; echo; echo 'required topics:'; for topic in /odom /odom_lio /scan_accumulated /map /local_costmap/costmap /global_costmap/costmap; do printf '%-32s ' \"\$topic\"; timeout 3 ros2 topic echo --once \"\$topic\" >/dev/null 2>&1 && echo OK || echo MISSING; done; echo; echo 'Point-LIO delay:'; timeout -s INT 5 ros2 topic delay /odom_lio 2>&1 | tail -n 3 || true; echo; echo 'Navigation action:'; ros2 action list 2>/dev/null | grep navigate_to_pose || true; echo; echo '/cmd_vel endpoints:'; timeout 5 ros2 topic info /cmd_vel -v 2>&1 | grep -E 'Publisher count|Subscription count|Node name|Endpoint type' || true; echo; echo 'TF map -> base_footprint:'; timeout -s INT 4 ros2 run tf2_ros tf2_echo map base_footprint 2>&1 | head -n 14 || true; sleep 5; done"
tmux send-keys -t "$SESSION:status" "$status_cmd" C-m

echo "Navigation Two status tmux: $SESSION"
if [[ "$ATTACH" -eq 1 ]]; then
  if [[ -n "${TMUX:-}" ]]; then tmux switch-client -t "$SESSION"; else tmux attach -t "$SESSION"; fi
fi
