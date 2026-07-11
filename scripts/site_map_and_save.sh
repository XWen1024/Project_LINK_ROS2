#!/usr/bin/env bash
set -euo pipefail

WORKSPACE="${PROJECT_LINK_WORKSPACE:-/home/wte/wheeltec_robot}"
MAP_BASENAME="${PROJECT_LINK_SITE_MAP:-$WORKSPACE/maps/site_current}"
WAIT_FOR_MAP_TIMEOUT="${WAIT_FOR_MAP_TIMEOUT:-90}"
ATTACH=0
RESTART=0

usage() {
  cat <<EOF
Usage: ./scripts/site_map_and_save.sh [--restart] [--attach] [--map PATH_WITHOUT_EXTENSION]

Starts the known-good SLAM tmux stack, waits for /map, lets you inspect/scan in
RViz, then saves the current map with nav2_map_server map_saver_cli.

Options:
  --restart                 Restart the SLAM tmux session before mapping.
  --attach                  Attach to the SLAM tmux session after start.
  --map PATH_WITHOUT_EXT    Default: $MAP_BASENAME
  -h, --help                Show help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --restart) RESTART=1 ;;
    --attach) ATTACH=1 ;;
    --map) MAP_BASENAME="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

cd "$WORKSPACE"
source scripts/project_link_env.sh

mkdir -p "$(dirname "$MAP_BASENAME")"

slam_args=(--no-attach)
if [[ "$RESTART" -eq 1 ]]; then
  slam_args=(--restart --no-attach)
fi

echo "[1/4] Starting SLAM tmux stack..."
./start_slam_tmux.sh "${slam_args[@]}"

echo "[2/4] Waiting for /map for up to ${WAIT_FOR_MAP_TIMEOUT}s..."
timeout "$WAIT_FOR_MAP_TIMEOUT" ros2 topic echo --once /map --field header >/dev/null

cat <<EOF

[3/4] /map is publishing.
Open RViz2 from your visualization computer, set Fixed Frame to map, and walk/drive
the robot slowly until the map is good.

When the map looks good, press Enter here to save:
  $MAP_BASENAME.yaml
  $MAP_BASENAME.pgm

EOF
read -r

echo "[4/4] Saving map..."
ros2 run nav2_map_server map_saver_cli -f "$MAP_BASENAME"
echo "Saved map: $MAP_BASENAME.yaml"

if [[ "$ATTACH" -eq 1 ]]; then
  tmux attach -t "${PROJECT_LINK_TMUX_SESSION:-project_link_slam}"
fi
