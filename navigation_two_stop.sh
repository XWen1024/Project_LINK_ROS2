#!/usr/bin/env bash
set -euo pipefail

WORKSPACE="${PROJECT_LINK_WORKSPACE:-/home/wte/wheeltec_robot}"
cd "$WORKSPACE"
source scripts/project_link_env.sh

timeout 3 ros2 topic pub --once /cmd_vel geometry_msgs/msg/Twist '{}' >/dev/null 2>&1 || true
timeout 3 ros2 topic pub --once /cmd_vel_nav geometry_msgs/msg/Twist '{}' >/dev/null 2>&1 || true

for session in \
  "${NAVIGATION_TWO_STATUS_SESSION:-project_link_navigation_two_status}" \
  "${NAVIGATION_TWO_SAVE_SESSION:-project_link_navigation_two_save}" \
  "${PROJECT_LINK_NAV2_TMUX_SESSION:-project_link_point_lio_nav2}" \
  "${PROJECT_LINK_TMUX_SESSION:-project_link_point_lio}" \
  "${PROJECT_LINK_BASE_TMUX_SESSION:-project_link_c63_base}"; do
  tmux kill-session -t "$session" 2>/dev/null || true
done

pkill -f 'point_lio_navigation.launch.py' || true
pkill -f '/nav2_controller/controller_server' || true
pkill -f '/nav2_smoother/smoother_server' || true
pkill -f '/nav2_planner/planner_server' || true
pkill -f '/nav2_behaviors/behavior_server' || true
pkill -f '/nav2_bt_navigator/bt_navigator' || true
pkill -f '/nav2_waypoint_follower/waypoint_follower' || true
pkill -f '/nav2_velocity_smoother/velocity_smoother' || true
pkill -f 'lifecycle_manager_navigation' || true
pkill -f 'point_lio_unilidar_l1.launch.py' || true
pkill -f 'pointlio_mapping' || true
pkill -f 'async_slam_toolbox_node' || true
pkill -f 'laser_scan_accumulator' || true
pkill -f 'unilidar_p2s.launch.py' || true
pkill -f 'pointcloud_to_laserscan_node' || true
pkill -f 'unitree_lidar_ros2_node' || true
pkill -f 'robot_mode_description.launch.py' || true
pkill -f 'robot_state_publisher' || true
pkill -f 'base_serial.launch.py' || true
pkill -f 'wheeltec_robot_node' || true

echo "Navigation Two stack stopped."
