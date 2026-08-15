# Project LINK ROS 2 Agent Notes

This file is the short repository-level operating contract. Detailed module
context belongs in `docs/modules/`; historical context belongs in `docs/archive/`.

## Current Priority

- Current phase: build the Ubuntu 22.04 PySide6 control console and migrate Orin
  production lifecycle management from tmux to `systemd --user`.
- Immediate order:
  1. Build and verify the console interfaces, agent and GUI on Ubuntu/Orin.
  2. Install and validate the versioned systemd user units without auto-starting hardware.
  3. Validate the navigation/mapping page, goal control and dead-man teleoperation.
  4. Integrate the existing Ubuntu visual-grasp client.
  5. Add classic/Qwen voice switching, timing events and configuration editing.
  6. Add UWB shadow visualization and calibration capture.
- Keep existing scripts as fallback until systemd passes two complete supervised
  field-validation cycles.

## Canonical Documentation

- Documentation index: `docs/README.md`
- System boundary: `docs/architecture/SYSTEM_OVERVIEW.md`
- Console architecture: `docs/architecture/CONSOLE_ARCHITECTURE.md`
- Navigation: `docs/modules/navigation/HANDOFF.md`
- Manipulation: `docs/modules/manipulation/HANDOFF.md`
- Voice: `docs/modules/voice/OVERVIEW.md`
- UWB: `docs/modules/uwb/HANDOFF.md`
- VL53L0X: `docs/modules/sensors/vl53l0x/HANDOFF.md`

Read the relevant current handoff before changing a module. Archived documents
are evidence snapshots, not current operating instructions.

## System Boundary

- Jetson Orin Nano at `wte@orin` owns all hardware access and robot control.
- Orin workspace: `/home/wte/wheeltec_robot`.
- Ubuntu 22.04 laptop owns all GUI, video, chart, map and RViz2 rendering.
- Both computers use ROS 2 Humble, `ROS_DOMAIN_ID=42`, `ROS_LOCALHOST_ONLY=0`.
- Do not move camera, SO-101, audio, UWB or serial ownership into the Ubuntu GUI.

## Linux Validation And SSH Rules

- The Windows development machine is for editing, Git work and platform-neutral
  tests only. Do not install or run PySide6 there, do not use WSL as a substitute,
  and do not attempt real Qt/RViz window rendering on Windows.
- Run Linux-only checks such as PySide6 rendering, ROS 2 builds, Bash validation,
  `systemd-analyze --user verify` and user-unit tests directly on the target Linux
  machines over SSH.
- Tell the user before initiating any SSH connection and identify the target.
  The Orin is normally powered on during coding sessions. The Ubuntu laptop may
  be off, so ask the user to power it on before a validation step that requires it.
- Prefer a system-level/escalated SSH command instead of sandboxed SSH. The
  sandbox may not see the user's SSH aliases, keys or agent state reliably.
- Use `wte@orin` for the Orin. Do not invent an Ubuntu hostname or address; use
  the user-provided SSH target when the Ubuntu laptop is needed.

## Active ROS Packages

- Console: `project_link_console_interfaces`, `project_link_console_agent`,
  `project_link_console_gui`.
- Navigation/base: `turn_on_wheeltec_robot`, `wheeltec_nav2`,
  `wheeltec_slam_toolbox`, `rf2o_laser_odometry`, `wheeltec_robot_msg`.
- Voice: `project_link_voice`, `project_link_qwen_realtime_voice`,
  `project_link_voice_interfaces`.
- Manipulation: `project_link_visual_grasp`, `project_link_visual_grasp_gui`,
  `project_link_vl53l0x`.
- UWB: `project_link_uwb_interfaces`, `project_link_uwb_navigation`.
- Fall response: `project_link_emergency_interfaces`, `project_link_fall_response`.

## Core Safety Rules

- Do not start motion automatically during bringup, install or GUI startup.
- Before any physical motion, clear the area and keep the real E-stop or power
  cut available.
- Only one `/cmd_vel` control path may be active. Navigation uses Nav2; console
  teleoperation is mapping-only and must be disabled before Nav2 starts.
- The GUI must never publish `/cmd_vel` directly. The Orin agent owns the
  teleop publisher and must stop within 250 ms after dead-man, heartbeat, focus,
  network or mode loss.
- Do not run rf2o and Point-LIO stacks together; only one stack may own
  `odom -> base_footprint`.
- Qwen and classic voice are mutually exclusive because they share wake serial,
  microphone, speaker and robot tools.
- LLMs choose registered tools only. Python owns confirmation, validation,
  ROS Actions, cancellation and all robot execution.
- UWB defaults to shadow. Live motion requires approved calibration and the
  existing guarded Nav2 safety gates.
- Orin exclusively owns the SO-101, RGB camera and VL53L0X serial ports.
- Never use increased joint limits, blind travel or timeouts to conceal a grasp failure.

## Console Architecture Rules

- Use PySide6 with restrained native/Fusion controls and Chinese operator labels.
- Provide a global simple/advanced mode; raw parameter names belong in advanced
  tooltips, not the default interface.
- Use a built-in 2D map/costmap/scan renderer. Launch a separate configured
  RViz2 process for 3D point-cloud and TF diagnostics.
- The Orin console agent may control only an explicit allowlist of systemd units.
  Never accept arbitrary unit names or shell commands from GUI input.
- API keys remain in mode-0600 Orin files and are edited through an allowlisted
  SSH configuration helper. Do not send secret values in ordinary ROS messages.
- Volcengine Embedded Kit S2S is archived and must not appear as a normal console backend.

## Update And Git Rules

- GitHub is the source of truth for repository files.
- `main` is the sole long-lived integration and development branch. Work directly
  on the latest `main` unless the user explicitly requests isolation.
- Do not create feature, experiment or worktree branches by default.
- Needed work from old branches must be merged into the latest `main`; experimental,
  superseded and unnecessary branches must be preserved by annotated `archive/*`
  tags or refs and removed as active development lines.
- Do not commit speculative work merely to save it. Keep uncertain experiments
  uncommitted while testing; discard only the experiment if it is not useful.
  Commit a coherent change directly on `main` once it is useful and verified.
- Never rewrite published `main` history for appearance. Use forward commits,
  merges, tags and cleanup.
- Edit and test locally, commit, push to GitHub, then update Orin with
  `git pull --ff-only`. Do not replace repository files with `scp`.
- Do not commit build/install/log folders, bags, temporary maps, generated audio,
  private hardware captures or secrets.

## Documentation And Encoding

- When launch flows, hardware assumptions, network settings or milestone priority
  change, update `AGENTS.md`, `PROGRESS.md`, `README.md` and the relevant module handoff.
- Current handoffs must state verification date, canonical commit and remaining gates.
- Markdown is UTF-8. In Windows PowerShell use `Get-Content -Encoding UTF8`.
- Prefer `rg` for searches. Verify UTF-8 before treating displayed mojibake as corruption.
