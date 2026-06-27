#!/usr/bin/env bash

export ROS_DOMAIN_ID=42
export ROS_LOCALHOST_ONLY=0
source /opt/ros/humble/setup.bash
if [ -f /home/wte/point_lio_ws/install/setup.bash ]; then
  source /home/wte/point_lio_ws/install/setup.bash
fi
source /home/wte/wheeltec_robot/install/setup.bash
