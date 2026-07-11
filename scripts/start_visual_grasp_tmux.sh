#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ./start_visual_grasp_tmux.sh [--restart] [--config PATH]

Starts the headless YOLO-World + SO-101 visual-grasp node on Orin.
It never starts Nav2 and never publishes /cmd_vel.
EOF
}

restart=false
config_path="${VISUAL_GRASP_CONFIG:-$HOME/wheeltec_robot/configs/visual_grasp/visual_grasp.yaml}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --restart) restart=true ;;
    --config) config_path="$2"; shift ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
source scripts/project_link_env.sh

if [[ ! -f "$config_path" ]]; then
  echo "Visual-grasp config not found: $config_path" >&2
  exit 1
fi

session="project_link_visual_grasp"
if tmux has-session -t "$session" 2>/dev/null; then
  if ! $restart; then
    echo "tmux session '$session' already exists. Use --restart to replace it." >&2
    exit 1
  fi
  tmux kill-session -t "$session"
fi

command="cd '$repo_root' && source scripts/project_link_env.sh && ros2 launch project_link_visual_grasp visual_grasp.launch.py config:='$config_path'"
tmux new-session -d -s "$session" -n visual-grasp "$command"
echo "Started headless visual grasp in tmux session '$session'."
echo "Run the Ubuntu GUI separately: ros2 run project_link_visual_grasp_gui visual_grasp_gui"