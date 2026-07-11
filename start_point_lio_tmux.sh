#!/usr/bin/env bash
set -euo pipefail

SESSION="${PROJECT_LINK_TMUX_SESSION:-project_link_point_lio}"
WORKSPACE="${PROJECT_LINK_WORKSPACE:-/home/wte/wheeltec_robot}"
POINT_LIO_WS="${POINT_LIO_WS:-/home/wte/point_lio_ws}"
UNILIDAR_WS="${UNILIDAR_WS:-/home/wte/unilidar_sdk/unitree_lidar_ros2}"
LIDAR_PORT="${UNILIDAR_PORT:-/dev/unilidar}"
ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-42}"
ROS_LOCALHOST_ONLY="${ROS_LOCALHOST_ONLY:-0}"
WAIT_FOR_LIDAR_TIMEOUT="${WAIT_FOR_LIDAR_TIMEOUT:-30}"
LIDAR_TF_X="${LIDAR_TF_X:-0}"
LIDAR_TF_Y="${LIDAR_TF_Y:-0}"
LIDAR_TF_Z="${LIDAR_TF_Z:-0}"
LIDAR_TF_ROLL="${LIDAR_TF_ROLL:-3.14159}"
LIDAR_TF_PITCH="${LIDAR_TF_PITCH:-0.0}"
LIDAR_TF_YAW="${LIDAR_TF_YAW:-0.44041}"

ATTACH=1
CLEAN=0
STOP=0
WITH_2D_MAP=0

usage() {
  cat <<EOF
Usage: ./start_point_lio_tmux.sh [options]

Start Project LINK Point-LIO bringup in a tmux session.

Options:
  --with-2d-map  Also start pointcloud_to_laserscan and slam_toolbox.
  --restart      Stop the tmux session and clean known SLAM/lidar processes first.
  --clean        Clean known SLAM/lidar processes before starting.
  --stop         Stop only the tmux session.
  --no-attach    Start the session but do not attach to it.
  --attach       Attach to the session after starting. This is the default.
  -h, --help     Show this help.

Environment overrides:
  PROJECT_LINK_TMUX_SESSION   default: project_link_point_lio
  PROJECT_LINK_WORKSPACE      default: /home/wte/wheeltec_robot
  POINT_LIO_WS                default: /home/wte/point_lio_ws
  UNILIDAR_WS                 default: /home/wte/unilidar_sdk/unitree_lidar_ros2
  UNILIDAR_PORT               default: /dev/unilidar
  ROS_DOMAIN_ID               default: 42
  ROS_LOCALHOST_ONLY          default: 0
  WAIT_FOR_LIDAR_TIMEOUT      default: 30
  LIDAR_TF_X/Y/Z              default: 0 / 0 / 0
  LIDAR_TF_ROLL/PITCH/YAW     default: 3.14159 / 0.0 / 0.44041
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-2d-map)
      WITH_2D_MAP=1
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
  echo "[clean] Stopping known Project LINK lidar/SLAM processes..."
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
  pkill -f 'point_lio_unilidar_l1.launch.py' || true
  pkill -f 'pointlio_mapping' || true

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

if [[ ! -d "$POINT_LIO_WS" ]]; then
  echo "Point-LIO workspace not found: $POINT_LIO_WS" >&2
  echo "Create it first with the setup steps in README.md." >&2
  exit 1
fi

if [[ ! -d "$UNILIDAR_WS" ]]; then
  echo "UniLidar workspace not found: $UNILIDAR_WS" >&2
  exit 1
fi

if [[ ! -e "$LIDAR_PORT" ]]; then
  echo "Warning: lidar port does not exist yet: $LIDAR_PORT" >&2
fi

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "Session already exists: $SESSION"
  attach_session
  exit 0
fi

tmux new-session -d -s "$SESSION" -n lidar

lidar_cmd="cd '$UNILIDAR_WS' && export ROS_DOMAIN_ID='$ROS_DOMAIN_ID' ROS_LOCALHOST_ONLY='$ROS_LOCALHOST_ONLY' && source /opt/ros/humble/setup.bash && source install/setup.bash && ros2 run unitree_lidar_ros2 unitree_lidar_ros2_node --ros-args -p port:='$LIDAR_PORT'"
description_cmd="cd '$WORKSPACE' && source scripts/project_link_env.sh && ros2 launch turn_on_wheeltec_robot robot_mode_description.launch.py"
wait_lidar_cmd="wait_for_topic() { local topic=\"\$1\"; local timeout_s=\"\$2\"; echo \"[wait] Waiting for one message on \${topic} for up to \${timeout_s}s...\"; timeout \"\${timeout_s}\" ros2 topic echo --once \"\${topic}\" --field header >/dev/null; }; wait_for_topic /unilidar/cloud '$WAIT_FOR_LIDAR_TIMEOUT' && wait_for_topic /unilidar/imu '$WAIT_FOR_LIDAR_TIMEOUT'"
lidar_tf_args="lidar_tf_x:=$LIDAR_TF_X lidar_tf_y:=$LIDAR_TF_Y lidar_tf_z:=$LIDAR_TF_Z lidar_tf_roll:=$LIDAR_TF_ROLL lidar_tf_pitch:=$LIDAR_TF_PITCH lidar_tf_yaw:=$LIDAR_TF_YAW"
scan_cmd="cd '$WORKSPACE' && source scripts/project_link_env.sh && $wait_lidar_cmd && ros2 launch turn_on_wheeltec_robot unilidar_p2s.launch.py $lidar_tf_args"
lio_launch_args="enable_slam_toolbox:=false publish_lidar_static_tf:=true $lidar_tf_args"
check_topics="/unilidar/cloud /unilidar/imu /odom_lio_raw /odom_lio /point_lio/cloud_registered"
if [[ "$WITH_2D_MAP" -eq 1 ]]; then
  lio_launch_args="enable_slam_toolbox:=true publish_lidar_static_tf:=false $lidar_tf_args"
  check_topics="$check_topics /scan /map"
fi
lio_cmd="cd '$WORKSPACE' && source scripts/project_link_env.sh && $wait_lidar_cmd && ros2 launch turn_on_wheeltec_robot point_lio_unilidar_l1.launch.py $lio_launch_args"
check_cmd="cd '$WORKSPACE' && source scripts/project_link_env.sh && echo 'Mode: Point-LIO Phase A raw 3D LIO plus planar base projection. Do not use --with-2d-map until Phase A TF validation passes.'; if [[ '$WITH_2D_MAP' -eq 1 ]]; then echo 'Mode: Point-LIO Phase B odometry + 2D map.'; fi; while true; do clear; date; echo; echo 'Nodes:'; ros2 node list 2>/dev/null | sort || true; echo; for topic in $check_topics; do echo \"=== \$topic ===\"; timeout -s INT 4 ros2 topic hz \"\$topic\" 2>&1 | grep -E 'average rate|WARNING|error|does not appear' | tail -n 3 || true; done; echo; echo 'TF lio_odom -> lio_base (raw 3D):'; timeout -s INT 4 ros2 run tf2_ros tf2_echo lio_odom lio_base 2>&1 | head -n 14 || true; echo; echo 'TF odom -> base_footprint (projected planar):'; timeout -s INT 4 ros2 run tf2_ros tf2_echo odom base_footprint 2>&1 | head -n 14 || true; if [[ '$WITH_2D_MAP' -eq 1 ]]; then echo; echo 'TF map -> odom:'; timeout -s INT 4 ros2 run tf2_ros tf2_echo map odom 2>&1 | head -n 14 || true; fi; echo; echo \"TF tuning: LIDAR_TF_ROLL=$LIDAR_TF_ROLL LIDAR_TF_PITCH=$LIDAR_TF_PITCH LIDAR_TF_YAW=$LIDAR_TF_YAW\"; echo 'Ctrl-C stops this monitor only. Use tmux kill-session -t $SESSION to stop all panes.'; sleep 5; done"

send "$SESSION:lidar" "$lidar_cmd"

tmux new-window -t "$SESSION" -n robot
send "$SESSION:robot.0" "$description_cmd"
if [[ "$WITH_2D_MAP" -eq 1 ]]; then
  tmux split-window -v -t "$SESSION:robot"
  send "$SESSION:robot.1" "$scan_cmd"
  tmux select-layout -t "$SESSION:robot" even-vertical >/dev/null
fi

tmux new-window -t "$SESSION" -n lio
send "$SESSION:lio" "$lio_cmd"

tmux new-window -t "$SESSION" -n check
send "$SESSION:check" "$check_cmd"

tmux select-window -t "$SESSION:check"

attach_session
