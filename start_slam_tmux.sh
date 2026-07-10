#!/usr/bin/env bash
set -euo pipefail

SESSION="${PROJECT_LINK_TMUX_SESSION:-project_link_slam}"
WORKSPACE="${PROJECT_LINK_WORKSPACE:-/home/wte/wheeltec_robot}"
UNILIDAR_WS="${UNILIDAR_WS:-/home/wte/unilidar_sdk/unitree_lidar_ros2}"
LIDAR_PORT="${UNILIDAR_PORT:-/dev/unilidar}"
ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-42}"
ROS_LOCALHOST_ONLY="${ROS_LOCALHOST_ONLY:-0}"
WAIT_FOR_BASE_TIMEOUT="${WAIT_FOR_BASE_TIMEOUT:-30}"
WAIT_FOR_SCAN_TIMEOUT="${WAIT_FOR_SCAN_TIMEOUT:-30}"

ATTACH=1
CLEAN=0
STOP=0
START_BASE=1

usage() {
  cat <<EOF
Usage: ./start_slam_tmux.sh [options]

Start the current SLAM-first bringup in a tmux session.
By default this starts the C63A base serial node, lidar, robot description,
rf2o, EKF, and slam_toolbox.

Options:
  --no-base       Do not start C63A base_serial.launch.py or wait for /odom.
  --restart       Stop the tmux session and clean known SLAM/lidar processes first.
  --clean         Clean known SLAM/lidar processes before starting.
  --stop          Stop only the tmux session.
  --no-attach     Start the session but do not attach to it.
  --attach        Attach to the session after starting. This is the default.
  -h, --help      Show this help.

Environment overrides:
  PROJECT_LINK_TMUX_SESSION   default: project_link_slam
  PROJECT_LINK_WORKSPACE      default: /home/wte/wheeltec_robot
  UNILIDAR_WS                 default: /home/wte/unilidar_sdk/unitree_lidar_ros2
  UNILIDAR_PORT               default: /dev/unilidar
  ROS_DOMAIN_ID               default: 42
  ROS_LOCALHOST_ONLY          default: 0
  WAIT_FOR_BASE_TIMEOUT       default: 30
  WAIT_FOR_SCAN_TIMEOUT       default: 30
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-base)
      START_BASE=0
      ;;
    --restart)
      CLEAN=1
      STOP=1
      ;;
    --clean)
      CLEAN=1
      ;;
    --stop)
      STOP=1
      ;;
    --no-attach)
      ATTACH=0
      ;;
    --attach)
      ATTACH=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing command: $1" >&2
    if [[ "$1" == "tmux" ]]; then
      echo "Install it on Orin with: sudo apt update && sudo apt install -y tmux" >&2
    fi
    exit 1
  fi
}

kill_tmux_session() {
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    tmux kill-session -t "$SESSION"
  fi
}

clean_ros_processes() {
  echo "[clean] Stopping known Project LINK base/SLAM/lidar processes..."
  pkill -f 'base_serial.launch.py' || true
  pkill -f 'wheeltec_robot_node' || true
  pkill -f 'unitree_lidar_ros2_node' || true
  pkill -f 'unilidar_p2s.launch.py' || true
  pkill -f 'pointcloud_to_laserscan_node' || true
  pkill -f 'unilidar_tf_broadcaster' || true
  pkill -f 'robot_mode_description.launch.py' || true
  pkill -f 'robot_state_publisher' || true
  pkill -f 'rf2o_slam_toolbox.launch.py' || true
  pkill -f 'rf2o_laser_odometry_node' || true
  pkill -f 'ekf_odom_fusion' || true
  pkill -f 'async_slam_toolbox_node' || true

  if command -v ros2 >/dev/null 2>&1; then
    ros2 daemon stop >/dev/null 2>&1 || true
  fi
}

attach_session() {
  echo
  echo "tmux session is ready: $SESSION"
  echo "Attach later with: tmux attach -t $SESSION"
  echo "Stop it with:    tmux kill-session -t $SESSION"
  echo

  if [[ "$ATTACH" -eq 1 ]]; then
    if [[ -n "${TMUX:-}" ]]; then
      tmux switch-client -t "$SESSION"
    else
      tmux attach -t "$SESSION"
    fi
  fi
}

send() {
  local target="$1"
  local command="$2"
  tmux send-keys -t "$target" "$command" C-m
}

require_cmd tmux

if [[ "$STOP" -eq 1 ]]; then
  kill_tmux_session
fi

if [[ "$CLEAN" -eq 1 ]]; then
  clean_ros_processes
fi

if [[ "$STOP" -eq 1 && "$CLEAN" -eq 0 ]]; then
  echo "Stopped tmux session: $SESSION"
  exit 0
fi

if [[ ! -d "$WORKSPACE" ]]; then
  echo "Workspace not found: $WORKSPACE" >&2
  exit 1
fi

if [[ ! -d "$UNILIDAR_WS" ]]; then
  echo "UniLidar workspace not found: $UNILIDAR_WS" >&2
  exit 1
fi

if [[ ! -e "$LIDAR_PORT" ]]; then
  echo "Warning: lidar port does not exist yet: $LIDAR_PORT" >&2
fi

if [[ "$START_BASE" -eq 1 && ! -e /dev/wheeltec_controller ]]; then
  echo "Warning: C63A base alias does not exist yet: /dev/wheeltec_controller" >&2
fi

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "Session already exists: $SESSION"
  attach_session
  exit 0
fi

tmux new-session -d -s "$SESSION" -n lidar

base_cmd="cd '$WORKSPACE' && source scripts/project_link_env.sh && ros2 launch turn_on_wheeltec_robot base_serial.launch.py"
lidar_cmd="cd '$UNILIDAR_WS' && export ROS_DOMAIN_ID='$ROS_DOMAIN_ID' ROS_LOCALHOST_ONLY='$ROS_LOCALHOST_ONLY' && source /opt/ros/humble/setup.bash && source install/setup.bash && ros2 run unitree_lidar_ros2 unitree_lidar_ros2_node --ros-args -p port:='$LIDAR_PORT'"
scan_cmd="cd '$WORKSPACE' && source scripts/project_link_env.sh && ros2 launch turn_on_wheeltec_robot unilidar_p2s.launch.py"
description_cmd="cd '$WORKSPACE' && source scripts/project_link_env.sh && ros2 launch turn_on_wheeltec_robot robot_mode_description.launch.py"
wait_scan_cmd="wait_for_topic() { local topic=\"\$1\"; local timeout_s=\"\$2\"; echo \"[wait] Waiting for one message on \${topic} for up to \${timeout_s}s...\"; timeout \"\${timeout_s}\" ros2 topic echo --once \"\${topic}\" --field header >/dev/null; }; wait_for_topic /scan '$WAIT_FOR_SCAN_TIMEOUT'"
wait_base_cmd="wait_for_topic() { local topic=\"\$1\"; local timeout_s=\"\$2\"; echo \"[wait] Waiting for one message on \${topic} for up to \${timeout_s}s...\"; timeout \"\${timeout_s}\" ros2 topic echo --once \"\${topic}\" --field header >/dev/null; }; wait_for_topic /odom '$WAIT_FOR_BASE_TIMEOUT'"
if [[ "$START_BASE" -eq 1 ]]; then
  slam_wait_cmd="$wait_base_cmd && $wait_scan_cmd"
  check_topics="/odom /imu/data_raw /PowerVoltage /unilidar/cloud /scan /odom_rf2o /odometry/filtered /map"
else
  slam_wait_cmd="$wait_scan_cmd"
  check_topics="/unilidar/cloud /scan /odom_rf2o /odometry/filtered /map"
fi
slam_cmd="cd '$WORKSPACE' && source scripts/project_link_env.sh && $slam_wait_cmd && ros2 launch turn_on_wheeltec_robot rf2o_slam_toolbox.launch.py"
check_cmd="cd '$WORKSPACE' && source scripts/project_link_env.sh && echo 'Mode: rf2o + EKF + slam_toolbox. START_BASE=$START_BASE'; while true; do clear; date; echo; echo 'Nodes:'; ros2 node list 2>/dev/null | sort || true; echo; for topic in $check_topics; do echo \"=== \$topic ===\"; timeout -s INT 4 ros2 topic hz \"\$topic\" 2>&1 | grep -E 'average rate|WARNING|error|does not appear' | tail -n 3 || true; done; echo; echo 'TF odom -> base_footprint:'; timeout -s INT 4 ros2 run tf2_ros tf2_echo odom base_footprint 2>&1 | head -n 14 || true; echo; echo 'TF map -> odom:'; timeout -s INT 4 ros2 run tf2_ros tf2_echo map odom 2>&1 | head -n 14 || true; echo; echo 'Ctrl-C stops this monitor only. Use tmux kill-session -t $SESSION to stop all panes.'; sleep 5; done"

send "$SESSION:lidar" "$lidar_cmd"

if [[ "$START_BASE" -eq 1 ]]; then
  tmux new-window -t "$SESSION" -n base
  send "$SESSION:base" "$base_cmd"
fi

tmux new-window -t "$SESSION" -n robot
send "$SESSION:robot.0" "$scan_cmd"
tmux split-window -v -t "$SESSION:robot"
send "$SESSION:robot.1" "$description_cmd"
tmux select-layout -t "$SESSION:robot" even-vertical >/dev/null

tmux new-window -t "$SESSION" -n slam
send "$SESSION:slam" "$slam_cmd"

tmux new-window -t "$SESSION" -n check
send "$SESSION:check" "$check_cmd"

tmux select-window -t "$SESSION:check"

attach_session
