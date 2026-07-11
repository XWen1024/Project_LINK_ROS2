#!/usr/bin/env bash
set -euo pipefail

WORKSPACE="${PROJECT_LINK_WORKSPACE:-/home/wte/wheeltec_robot}"
WAYPOINTS="${PROJECT_LINK_VOICE_WAYPOINTS:-/home/wte/.ros/project_link_voice/waypoints.json}"

usage() {
  cat <<EOF
Usage:
  ./scripts/site_waypoints.sh list
  ./scripts/site_waypoints.sh save-current NAME
  ./scripts/site_waypoints.sh save-click NAME [NAME ...]

Saves voice waypoint JSON for project_link_voice.
Default file: $WAYPOINTS

Notes:
  save-current saves the robot's current map -> base_footprint TF pose.
  save-click saves RViz /clicked_point positions in the order you click them.
EOF
}

if [[ $# -lt 1 ]]; then
  usage >&2
  exit 2
fi

cmd="$1"
shift

if [[ "$cmd" == "--help" || "$cmd" == "-h" ]]; then
  usage
  exit 0
fi

cd "$WORKSPACE"
source scripts/project_link_env.sh

case "$cmd" in
  list)
    python3 scripts/save_voice_waypoint.py --output "$WAYPOINTS" --list
    ;;
  save-current)
    if [[ $# -ne 1 ]]; then
      usage >&2
      exit 2
    fi
    python3 scripts/save_voice_waypoint.py --output "$WAYPOINTS" --from-tf "$1"
    ;;
  save-click)
    if [[ $# -lt 1 ]]; then
      usage >&2
      exit 2
    fi
    python3 scripts/save_voice_waypoint.py --output "$WAYPOINTS" --from-click --names "$@"
    ;;
  *)
    echo "Unknown command: $cmd" >&2
    usage >&2
    exit 2
    ;;
esac
