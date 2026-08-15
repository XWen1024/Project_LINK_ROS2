# Project LINK console GUI

Ubuntu 22.04 PySide6 client for the headless Orin console agent. The application
does all map, chart and future video rendering on the laptop. It never owns robot
hardware and never publishes `/cmd_vel`.

Build the first console slice on Ubuntu/Orin:

```bash
cd ~/wheeltec_robot
source /opt/ros/humble/setup.bash
python3 -m pip install --user -r src/project_link_console_gui/requirements-ubuntu.txt
colcon build --packages-select \
  project_link_console_interfaces \
  wheeltec_robot_msg \
  project_link_visual_grasp_gui \
  project_link_console_agent \
  project_link_console_gui
source install/setup.bash
```

Verified Ubuntu prerequisites are `git`, `python3-pip`,
`python3-colcon-common-extensions`, `ros-humble-navigation2` and
`ros-humble-nav2-bringup`. PySide6 is intentionally installed in the operator's
user Python environment rather than declared as a Jammy apt/rosdep dependency.

Laptop-only visual development:

```bash
ros2 run project_link_console_gui project_link_console --demo
```

Connect to the real Orin agent on ROS domain 42:

```bash
export ROS_DOMAIN_ID=42
export ROS_LOCALHOST_ONLY=0
ros2 run project_link_console_gui project_link_console
```

Opening the GUI starts no hardware, stack, goal or velocity. Mapping/navigation
buttons call the typed Orin lifecycle Action. Mapping teleop sends a 20 Hz lease
to the agent only while this page owns keyboard focus and Space plus W/A/S/D is
held. The agent remains the only component allowed to create the temporary
`/cmd_vel` publisher.

The manipulation page embeds `VisualGraspPanel` from the existing Ubuntu client.
Simple mode keeps raw Orin parameters hidden; advanced mode reveals the full
parameter editor. The embedded page creates only a ROS client and does not start
the Orin visual-grasp, arm or ToF services.
