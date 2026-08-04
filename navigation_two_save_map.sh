#!/usr/bin/env bash
set -euo pipefail

WORKSPACE="${PROJECT_LINK_WORKSPACE:-/home/wte/wheeltec_robot}"
MAP_DIR="${NAVIGATION_TWO_MAP_DIR:-/home/wte/maps}"
SESSION="${NAVIGATION_TWO_SAVE_SESSION:-project_link_navigation_two_save}"
WAIT_TIMEOUT="${NAVIGATION_TWO_WAIT_TIMEOUT:-60}"
MAP_BASENAME="$MAP_DIR/navigation_two_$(date +%Y%m%d_%H%M%S)"
ATTACH=0

usage() {
  cat <<EOF
Usage: ./navigation_two_save_map.sh [--name NAME | --map PATH] [--attach]
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --name) MAP_BASENAME="$MAP_DIR/$2"; shift ;;
    --map) MAP_BASENAME="$2"; shift ;;
    --attach) ATTACH=1 ;;
    --no-attach) ATTACH=0 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

if [[ ! "$MAP_BASENAME" =~ ^[A-Za-z0-9_./-]+$ ]]; then
  echo "Map path may contain only letters, numbers, '_', '-', '.', and '/'." >&2
  exit 2
fi

cd "$WORKSPACE"
set +u
source scripts/project_link_env.sh
set -u
mkdir -p "$(dirname "$MAP_BASENAME")"

echo "[wait] /map"
timeout "$WAIT_TIMEOUT" ros2 topic echo --once /map --field header >/dev/null

tmux kill-session -t "$SESSION" 2>/dev/null || true
tmux new-session -d -s "$SESSION" -n save

save_cmd="cd '$WORKSPACE' && source scripts/project_link_env.sh && echo 'Saving occupancy map and posegraph to $MAP_BASENAME'; timeout 20 ros2 service call /slam_toolbox/serialize_map slam_toolbox/srv/SerializePoseGraph \"{filename: '$MAP_BASENAME'}\" || echo 'Posegraph save skipped or failed'; ros2 run nav2_map_server map_saver_cli -f '$MAP_BASENAME'; echo 'Saved: $MAP_BASENAME.yaml'; exec bash"
tmux send-keys -t "$SESSION:save" "$save_cmd" C-m

for _ in $(seq 1 30); do
  if [[ -f "$MAP_BASENAME.yaml" ]]; then
    echo "Saved map: $MAP_BASENAME.yaml"
    [[ -f "$MAP_BASENAME.posegraph" ]] && echo "Saved posegraph: $MAP_BASENAME.posegraph"
    if [[ "$ATTACH" -eq 1 ]]; then tmux attach -t "$SESSION"; fi
    exit 0
  fi
  sleep 1
done

echo "Map save did not finish within 30 seconds." >&2
tmux capture-pane -pt "$SESSION:save" -S -120 >&2 || true
exit 1
