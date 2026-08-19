#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ./start_visual_grasp_tmux.sh [--restart] [--config PATH]
                                   [--with-tof] [--tof-config PATH]

Starts the headless YOLO-World + SO-101 visual-grasp node on Orin.
With --with-tof, also starts the ESP32-C3 VL53L0X serial range node.
It never starts Nav2 and never publishes /cmd_vel.
EOF
}

restart=false
with_tof=false
config_path="${VISUAL_GRASP_CONFIG:-$HOME/wheeltec_robot/configs/visual_grasp/visual_grasp.yaml}"
tof_config_path="${VL53L0X_CONFIG:-$HOME/wheeltec_robot/configs/vl53l0x/vl53l0x_gripper.yaml}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --restart) restart=true ;;
    --config) config_path="$2"; shift ;;
    --with-tof) with_tof=true ;;
    --tof-config) tof_config_path="$2"; shift ;;
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
if $with_tof && [[ ! -f "$tof_config_path" ]]; then
  echo "VL53L0X config not found: $tof_config_path" >&2
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
detector_command="cd '$repo_root' && exec deploy/systemd/bin/project-link-component visual-grasp-detector"
if $with_tof; then
  tof_command="cd '$repo_root' && source scripts/project_link_env.sh && ros2 launch project_link_vl53l0x vl53l0x_gripper.launch.py config:='$tof_config_path'"
  tmux new-session -d -s "$session" -n tof "$tof_command"
  tmux new-window -t "$session" -n cuda-detector "$detector_command"
  tmux new-window -t "$session" -n visual-grasp "$command"
else
  tmux new-session -d -s "$session" -n cuda-detector "$detector_command"
  tmux new-window -t "$session" -n visual-grasp "$command"
fi
echo "Started headless visual grasp in tmux session '$session'."
if $with_tof; then
  echo "VL53L0X range topic: /visual_grasp/tof_range"
fi
echo "Run the Ubuntu GUI separately: ros2 run project_link_visual_grasp_gui visual_grasp_gui"
