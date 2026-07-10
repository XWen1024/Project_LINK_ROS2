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
BASE_LOG="${BASE_LOG:-/tmp/c63_keyboard_teleop_base.log}"
START_BASE="${START_BASE:-1}"

cd "$WORKSPACE"
source install/setup.bash
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-42}"
export ROS_LOCALHOST_ONLY="${ROS_LOCALHOST_ONLY:-0}"

BASE_PID=""

publish_stop() {
  python3 - "$TOPIC" <<'PY' >/dev/null 2>&1 || true
import sys, time
import rclpy
from geometry_msgs.msg import Twist

topic = sys.argv[1]
rclpy.init()
node = rclpy.create_node("c63_keyboard_stop")
pub = node.create_publisher(Twist, topic, 10)
msg = Twist()
for _ in range(5):
    pub.publish(msg)
    rclpy.spin_once(node, timeout_sec=0.02)
    time.sleep(0.05)
node.destroy_node()
rclpy.shutdown()
PY
}

cleanup() {
  publish_stop
  if [[ -n "${TELEOP_PY:-}" && -f "$TELEOP_PY" ]]; then
    rm -f "$TELEOP_PY"
  fi
  if [[ -n "$BASE_PID" ]]; then
    kill "$BASE_PID" >/dev/null 2>&1 || true
    wait "$BASE_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

start_base_if_needed() {
  if ros2 node list 2>/dev/null | grep -qx '/wheeltec_robot'; then
    echo "[info] /wheeltec_robot already running."
    return
  fi

  if [[ "$START_BASE" != "1" ]]; then
    echo "[warn] /wheeltec_robot is not running, and START_BASE=0 was set." >&2
    return
  fi

  echo "[info] Starting base_serial.launch.py ..."
  ros2 launch turn_on_wheeltec_robot base_serial.launch.py >"$BASE_LOG" 2>&1 &
  BASE_PID="$!"
  sleep 5

  if ! ros2 node list 2>/dev/null | grep -qx '/wheeltec_robot'; then
    echo "[error] /wheeltec_robot did not start. Log:" >&2
    tail -n 100 "$BASE_LOG" >&2 || true
    exit 1
  fi
}

start_base_if_needed

TELEOP_PY="$(mktemp /tmp/c63_keyboard_teleop.XXXXXX.py)"
cat > "$TELEOP_PY" <<'PY'
import select
import signal
import sys
import termios
import time
import tty

import rclpy
from geometry_msgs.msg import Twist

topic = sys.argv[1]

linear = 0.0
angular = 0.0
linear_speed = 0.04
angular_speed = 0.20
linear_max = 0.60
angular_max = 1.50
rate_hz = 20.0
hold_s = 0.22
command_deadline = 0.0
running = True

def clamp(value, limit):
    return max(-limit, min(limit, value))

def stop_motion():
    global linear, angular
    linear = 0.0
    angular = 0.0

def speed_percent(value, limit):
    return round((value / limit) * 100)

def status_text(prefix=""):
    return (
        f"{prefix}cmd linear.x={linear:+.2f} m/s  angular.z={angular:+.2f} rad/s  "
        f"linear={linear_speed:.2f} ({speed_percent(linear_speed, linear_max)}%)  "
        f"angular={angular_speed:.2f} ({speed_percent(angular_speed, angular_max)}%)  "
        "space/x=STOP  Ctrl-C=exit"
    )

def help_text():
    return f"""
C63 keyboard teleop publishing to {topic}

Movement:
  w/s : forward/backward while held
  a/d : rotate left/right while held
  space or x : send zero velocity immediately

Speed:
  r/f : linear speed up/down    current {linear_speed:.2f} m/s ({speed_percent(linear_speed, linear_max)}%)
  t/g : angular speed up/down   current {angular_speed:.2f} rad/s ({speed_percent(angular_speed, angular_max)}%)

Other:
  h : show this help
  Ctrl-C : stop and exit

Dead-man mode: motion expires {hold_s:.2f}s after the last movement key.
Current command is published continuously at {rate_hz:.0f} Hz.
"""

def handle_keys(keys):
    global linear, angular, linear_speed, angular_speed, command_deadline, running
    if not keys:
        return

    if "\x03" in keys:
        running = False
        return

    for ch in keys:
        if ch == "h":
            print(help_text(), flush=True)

    adjusted = False
    for ch in keys:
        if ch == "r":
            linear_speed = min(linear_max, linear_speed + 0.01)
            adjusted = True
        elif ch == "f":
            linear_speed = max(0.01, linear_speed - 0.01)
            adjusted = True
        elif ch == "t":
            angular_speed = min(angular_max, angular_speed + 0.05)
            adjusted = True
        elif ch == "g":
            angular_speed = max(0.05, angular_speed - 0.05)
            adjusted = True

    if adjusted:
        stop_motion()
        command_deadline = 0.0
        print("\r" + status_text("[speed] "), end="", flush=True)
        return

    if any(ch in (" ", "x") for ch in keys):
        stop_motion()
        command_deadline = 0.0
        print("\r" + status_text("[stop]  "), end="", flush=True)
        return

    movement_keys = [ch for ch in keys if ch in ("w", "s", "a", "d")]
    if not movement_keys:
        return

    ch = movement_keys[-1]
    now = time.monotonic()
    if ch == "w":
        linear, angular = linear_speed, 0.0
    elif ch == "s":
        linear, angular = -linear_speed, 0.0
    elif ch == "a":
        linear, angular = 0.0, angular_speed
    elif ch == "d":
        linear, angular = 0.0, -angular_speed
    command_deadline = now + hold_s

def make_msg():
    msg = Twist()
    msg.linear.x = linear
    msg.linear.y = 0.0
    msg.angular.z = angular
    return msg

def on_signal(_signum, _frame):
    global running
    running = False

signal.signal(signal.SIGINT, on_signal)
signal.signal(signal.SIGTERM, on_signal)

rclpy.init()
node = rclpy.create_node("c63_keyboard_teleop")
pub = node.create_publisher(Twist, topic, 10)

old_settings = None
last_status = 0.0

try:
    if not sys.stdin.isatty():
        raise RuntimeError("stdin is not a TTY. Start this script with ssh -tt.")
    old_settings = termios.tcgetattr(sys.stdin)
    tty.setcbreak(sys.stdin.fileno())
    print(help_text(), flush=True)
    period = 1.0 / rate_hz
    while running and rclpy.ok():
        start = time.monotonic()
        keys = []
        while select.select([sys.stdin], [], [], 0.0)[0]:
            keys.append(sys.stdin.read(1))
        handle_keys(keys)

        now = time.monotonic()
        if command_deadline and now > command_deadline:
            stop_motion()
            command_deadline = 0.0
        try:
            pub.publish(make_msg())
            rclpy.spin_once(node, timeout_sec=0.0)
        except Exception as exc:
            print(f"\n[warn] ROS publish stopped: {exc}")
            break

        if keys or now - last_status > 0.25:
            print("\r" + status_text(), end="", flush=True)
            last_status = now

        elapsed = time.monotonic() - start
        if elapsed < period:
            time.sleep(period - elapsed)
finally:
    stop_motion()
    if rclpy.ok():
        for _ in range(8):
            try:
                pub.publish(make_msg())
                rclpy.spin_once(node, timeout_sec=0.02)
            except Exception:
                break
            time.sleep(0.03)
    if old_settings is not None:
        try:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
        except Exception:
            pass
    print("\n[ok] Stop published. Exiting.")
    node.destroy_node()
    if rclpy.ok():
        rclpy.shutdown()
PY
python3 "$TELEOP_PY" "$TOPIC"
