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

Only one real console instance may run per Ubuntu login. If the console is
already active, a second launch asks the operator to return to the existing
window instead of creating duplicate ROS nodes. The initial window size is
bounded to the desktop's available geometry. Use the button at the top of the
left sidebar, or `Ctrl+B`, to switch between the full navigation labels and the
compact icon-only sidebar.

Opening the GUI starts no hardware, stack, goal or velocity. Mapping/navigation
buttons call the typed Orin lifecycle Action. Mapping teleop sends a 20 Hz lease
to the agent only while this page owns keyboard focus and Space plus W/A/S/D is
held. The agent remains the only component allowed to create the temporary
`/cmd_vel` publisher.

The manipulation page embeds `VisualGraspPanel` from the existing Ubuntu client.
Simple mode keeps raw Orin parameters hidden; advanced mode reveals the full
parameter editor. The embedded page creates only a ROS client and does not start
the Orin visual-grasp, arm or ToF services.

The remaining pages are implemented as follows:

- Voice control: automatic plus manual Orin control-Action detection, mutually
  exclusive classic/Qwen switching, page-local operation results,
  wake/session/task state and sanitized per-stage timing from the Orin agent.
- Voice configuration: common VAD/audio values, separate system prompts and an
  editable registry limited to built-in Python tool executors.
- UWB: shadow-only start/stop, distance/angle view, distance/residual chart,
  common tuning and four-direction `proposed` calibration capture.
- Global settings: device/network values and masked classic/Qwen/UWB secrets.

Secrets use the fixed allowlisted helper over SSH stdin; they never travel over
ROS and are never read back in plaintext. Before using Read/Save on the laptop,
configure a separate Ubuntu-to-Orin key; do not copy the Windows private key:

```bash
ssh-keygen -t ed25519
ssh-copy-id wte@<Orin SSH target>
```

Then set the matching SSH target and `/home/wte/wheeltec_robot` workspace on the
Global Settings page. Saving configuration does not restart any service.

The SSH target above is only for configuration files. Voice start/stop uses ROS
2 directly: keep Ubuntu and Orin on the same LAN with `ROS_DOMAIN_ID=42` and
`ROS_LOCALHOST_ONLY=0`, open the Voice Control page, wait for the green
"Orin 语音控制已连接" state (or press "重新检测连接"), then start exactly one
backend. Starting a backend does not start Nav2, UWB or manipulation services.
