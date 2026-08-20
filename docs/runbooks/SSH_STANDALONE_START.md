# SSH Standalone Start

Status: current
Last reviewed: 2026-08-21

These entry points start production services through the Orin user systemd
manager. They do not call `project-link-console-agent.service`, do not require
the Ubuntu console to be running and do not depend on cross-host DDS for the
start request or readiness result.

From Windows using the established alias:

```powershell
ssh orin /home/wte/wheeltec_robot/scripts/standalone/start_nav2.sh
ssh orin /home/wte/wheeltec_robot/scripts/standalone/start_qwen_realtime.sh
ssh orin /home/wte/wheeltec_robot/scripts/standalone/start_fall_response.sh
```

From the Ubuntu laptop, use its configured SSH target, for example:

```bash
ssh wte@ubuntu.local /home/wte/wheeltec_robot/scripts/standalone/start_nav2.sh
ssh wte@ubuntu.local /home/wte/wheeltec_robot/scripts/standalone/start_qwen_realtime.sh
ssh wte@ubuntu.local /home/wte/wheeltec_robot/scripts/standalone/start_fall_response.sh
```

Or run the repository-owned client wrappers directly on Ubuntu:

```bash
./scripts/ssh_start_nav2.sh
./scripts/ssh_start_qwen_realtime.sh
./scripts/ssh_start_fall_response.sh
```

Override the target without editing a script:

```bash
PROJECT_LINK_ORIN_SSH_TARGET=wte@192.168.66.52 ./scripts/ssh_start_nav2.sh
```

Behavior:

- Nav2 starts `project-link-navigation.target` and waits through systemd for the
  base, lidar, robot description, scan, Point-LIO map and Nav2 services.
- Qwen starts `project-link-voice-qwen.service`. Its systemd conflict stops the
  classic backend if needed.
- Fall response starts `project-link-emergency.target`, including the fall
  coordinator, WeChat notifier and front camera. Although the target keeps the
  camera as a systemd `Wants`, the standalone script requires all three to be
  active before reporting the fall stack ready.

The scripts never send a navigation goal, Spin goal or nonzero velocity. ROS 2
services on the Orin still use local DDS/SHM internally because Nav2, voice tools
and fall response are ROS systems. Only the remote lifecycle control path is
SSH/systemd-only.
