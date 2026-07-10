#!/usr/bin/env bash
set -eo pipefail

SCRIPT_WORKSPACE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -z "${WORKSPACE:-}" ]]; then
  if [[ -f "$SCRIPT_WORKSPACE/install/setup.bash" ]]; then
    WORKSPACE="$SCRIPT_WORKSPACE"
  elif [[ -f /home/wte/wheeltec_robot/install/setup.bash ]]; then
    WORKSPACE="/home/wte/wheeltec_robot"
  else
    WORKSPACE="$SCRIPT_WORKSPACE"
  fi
fi
TOPIC="${TOPIC:-/cmd_vel}"
SPEED="${SPEED:-0.04}"
ANGULAR="${ANGULAR:-0.25}"
DURATION="${DURATION:-0.8}"
START_BASE=1
COMMAND="${1:-forward}"

usage() {
  cat <<'EOF'
Usage:
  ./scripts/c63_cmd_vel_once.sh [command] [options]

Commands:
  forward        Move forward briefly.
  backward       Move backward briefly.
  left           Strafe left briefly, for mecanum/omni chassis only.
  right          Strafe right briefly, for mecanum/omni chassis only.
  rotate-left    Rotate left briefly.
  rotate-right   Rotate right briefly.
  stop           Publish stop only.
  check          Start base node and check returned odom/IMU/power topics.

Options:
  --speed MPS       Linear speed in m/s. Default: 0.04
  --angular RADS    Angular speed in rad/s. Default: 0.25
  --duration SEC    Command duration before stop. Default: 0.8
  --topic NAME      Velocity topic. Default: /cmd_vel
  --no-start-base   Do not start base_serial.launch.py automatically.

Safety:
  Run only with wheels lifted, motor power disconnected, or a person at E-stop.
EOF
}

shift || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --speed)
      SPEED="$2"; shift 2 ;;
    --angular)
      ANGULAR="$2"; shift 2 ;;
    --duration)
      DURATION="$2"; shift 2 ;;
    --topic)
      TOPIC="$2"; shift 2 ;;
    --no-start-base)
      START_BASE=0; shift ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 2 ;;
  esac
done

cd "$WORKSPACE"
source install/setup.bash
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-42}"
export ROS_LOCALHOST_ONLY="${ROS_LOCALHOST_ONLY:-0}"

BASE_PID=""
cleanup() {
  publish_stop >/dev/null 2>&1 || true
  publish_stop >/dev/null 2>&1 || true
  if [[ -n "$BASE_PID" ]]; then
    kill "$BASE_PID" >/dev/null 2>&1 || true
    wait "$BASE_PID" >/dev/null 2>&1 || true
  fi
}

twist() {
  local x="$1" y="$2" z="$3"
  printf '{linear: {x: %s, y: %s, z: 0.0}, angular: {x: 0.0, y: 0.0, z: %s}}' "$x" "$y" "$z"
}

publish_once() {
  ros2 topic pub --once "$TOPIC" geometry_msgs/msg/Twist "$1"
}

publish_stop() {
  publish_once "$(twist 0.0 0.0 0.0)"
}

start_base_if_needed() {
  if ros2 node list 2>/dev/null | grep -qx '/wheeltec_robot'; then
    echo "[info] /wheeltec_robot already running."
    return
  fi

  if [[ "$START_BASE" -eq 0 ]]; then
    echo "[warn] /wheeltec_robot is not running, and --no-start-base was set." >&2
    return
  fi

  echo "[info] Starting base_serial.launch.py ..."
  ros2 launch turn_on_wheeltec_robot base_serial.launch.py >/tmp/c63_cmd_vel_once_base.log 2>&1 &
  BASE_PID="$!"
  sleep 5

  if ! ros2 node list 2>/dev/null | grep -qx '/wheeltec_robot'; then
    echo "[error] /wheeltec_robot did not start. Log:" >&2
    tail -n 80 /tmp/c63_cmd_vel_once_base.log >&2 || true
    exit 1
  fi
}

check_return_data() {
  start_base_if_needed
  echo "[check] Topics:"
  ros2 topic list | sort
  echo
  echo "[check] /odom once:"
  timeout 5s ros2 topic echo --once /odom 2>&1 | head -n 50 || true
  echo
  echo "[check] /imu/data_raw once:"
  timeout 5s ros2 topic echo --once /imu/data_raw 2>&1 | head -n 50 || true
  echo
  echo "[check] /PowerVoltage once:"
  timeout 5s ros2 topic echo --once /PowerVoltage 2>&1 | head -n 20 || true
}

case "$COMMAND" in
  forward)
    CMD="$(twist "$SPEED" 0.0 0.0)" ;;
  backward)
    CMD="$(twist "-$SPEED" 0.0 0.0)" ;;
  left)
    CMD="$(twist 0.0 "$SPEED" 0.0)" ;;
  right)
    CMD="$(twist 0.0 "-$SPEED" 0.0)" ;;
  rotate-left)
    CMD="$(twist 0.0 0.0 "$ANGULAR")" ;;
  rotate-right)
    CMD="$(twist 0.0 0.0 "-$ANGULAR")" ;;
  stop)
    start_base_if_needed
    publish_stop
    echo "[ok] Stop published on $TOPIC."
    exit 0 ;;
  check)
    trap cleanup EXIT
    check_return_data
    exit 0 ;;
  *)
    echo "Unknown command: $COMMAND" >&2
    usage
    exit 2 ;;
esac

trap cleanup EXIT INT TERM
start_base_if_needed

echo "[cmd] $COMMAND for ${DURATION}s on $TOPIC"
echo "[cmd] $CMD"
publish_once "$CMD"
sleep "$DURATION"
publish_stop
sleep 0.2
publish_stop
echo "[ok] Stop published."
