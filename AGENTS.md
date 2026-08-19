# Project LINK ROS 2 Agent Notes

This file is the short repository-level operating contract. Detailed module
context belongs in `docs/modules/`; historical context belongs in `docs/archive/`.

## Current Priority

- Current phase: build the Ubuntu 22.04 PySide6 control console and migrate Orin
  production lifecycle management from tmux to `systemd --user`.
- Immediate order:
  1. Build and verify the console interfaces, agent and GUI on Ubuntu/Orin.
  2. Install precise production hardware aliases and validate every connected device without motion.
  3. Add the Orin-owned front-camera stream to the navigation page.
  4. Replace direct cross-machine Wi-Fi DDS with DDS Router over an SSH tunnel.
  5. Complete one repeatable Qwen Realtime to Nav2 field loop.
- UWB is outside the current MVP. Keep its code and archived evidence, but hide
  the console page by default and do not spend MVP validation time on it.
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
- Ubuntu laptop workspace: `/home/xwen/wheeltec_robot`.
- Ubuntu PySide6 is a user-level pinned pip dependency from
  `src/project_link_console_gui/requirements-ubuntu.txt`; do not search for or
  install PySide6 on Windows.
- Treat the installed ROS 2 Humble Python API as authoritative. The current
  Ubuntu Humble build has no `rclpy.parameter_client`; GUI parameter access uses
  explicit `rcl_interfaces/srv/GetParameters` and `SetParameters` clients. Do
  not reintroduce a newer-distro import without verifying it on `ssh seewo`.
- Both computers use ROS 2 Humble, `ROS_DOMAIN_ID=42`, `ROS_LOCALHOST_ONLY=0`.
- Do not move camera, SO-101, audio, UWB or serial ownership into the Ubuntu GUI.
- The production cameras have separate roles: `/dev/project_link_front_camera`
  is the chassis-front preview and `/dev/project_link_arm_camera` is the
  manipulator/visual-grasp camera. Never use `/dev/videoN` in production config.
- Production serial aliases are `/dev/project_link_chassis`,
  `/dev/project_link_lidar`, `/dev/project_link_so101` and
  `/dev/project_link_wakeup`. Exact USB identities and host MAC addresses are
  recorded in `configs/hardware/orin-production.yaml`.
- Udev must match exact USB serials. A VID/PID-only chassis rule is forbidden:
  the voice wake board shares `1a86:55d4` and can otherwise be misidentified as
  the motor controller.

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
- Use the system SSH aliases `ssh orin` for the Orin Nano and `ssh seewo` for the
  Ubuntu laptop, with users `wte` and `xwen` respectively. The aliases intentionally
  point to the currently verified numeric DHCP addresses instead of relying on a
  permanent mDNS `ProxyCommand`. As of 2026-08-19 they are Orin `10.255.176.119`
  and Ubuntu `10.255.176.106`.
- Treat an SSH alias failure as a likely network/DHCP change before spending time
  debugging the old address. Autonomously inspect the active Windows subnet, scan
  that bounded subnet for SSH hosts, and use mDNS only to discover candidates
  (`ubuntu.local` for Orin and `XWen-P1430.local` for the laptop). Confirm the
  candidate using `hostname`, `/etc/machine-id` and the ED25519 host-key fingerprint,
  then update the alias `HostName` to the newly verified numeric IP and retry.
  Preserve the stable `HostKeyAlias` entries, do not install a permanent mDNS
  proxy/mapping, and do not keep diagnosing an obsolete IP after the verified host
  has moved. This discovery and SSH-config refresh is a Level 2 agent-owned action.
  Ask the user only when neither device can be found, the laptop may be powered off,
  or identity verification does not match.
- The Ubuntu GUI configuration channel is a separate Ubuntu-to-Orin SSH
  connection; Windows aliases and keys are not automatically available there.
  Never copy a Windows private key to the laptop. If BatchMode reports
  `Permission denied`, ask the user to create an Ubuntu user key and authorize
  only its public key on Orin, then set the SSH target in Global Settings.

### Failure Handling Levels

- Level 1 — agent-owned command errors: diagnose and retry autonomously. This
  includes quoting/escaping mistakes, unexpected EOF, PowerShell-to-Bash parsing,
  harmless path mistakes, incorrect flags, transient read-only timeouts and other
  non-destructive command-construction failures. Use multiple evidence-based
  attempts when useful; do not stop and ask the user to run these commands.
- Recurring PowerShell SSH rule: avoid a double-quoted remote command containing
  Bash `$variables`, nested quotes or command substitutions. Use a simple direct
  form for one command, such as `ssh orin hostname`. For a multi-line or quote-heavy
  Linux check, create a PowerShell single-quoted here-string, remove `\r`, encode
  its UTF-8 bytes with `[Convert]::ToBase64String(...)`, then run
  `ssh <alias> "echo <encoded> | base64 -d | bash"`. Directly piping a PowerShell
  string to `ssh ... bash -s` is not reliable because Windows may reinsert CRLF and
  give Linux tools arguments such as `1\r`. The Base64 single-line transport avoids
  both unmatched-quote/unexpected-EOF and CRLF corruption. Request each system-level
  SSH execution individually when approval is required.
- The Ubuntu laptop runs Mihomo on loopback port `7897`, but command-line Git and
  dependency tools do not automatically inherit it. When `ssh seewo` network
  operations fail, hang, report HTTP/2 framing errors or cannot reach an external
  package service, retry the network command with these temporary exports:
  `export https_proxy=http://127.0.0.1:7897`,
  `export http_proxy=http://127.0.0.1:7897`, and
  `export all_proxy=socks5://127.0.0.1:7897`. Prefer applying them only to the
  current remote shell or command. Do not write them into `.bashrc`, systemd
  units, ROS launch environments or repository files unless the user explicitly
  requests persistence. Do not assume the same loopback proxy exists on Orin;
  confirm it before using these values with `ssh orin`. If port `7897` refuses the
  connection, ask whether Mihomo is running instead of cycling through guessed
  proxy ports.
- Level 2 — safe remote/environment failures: continue with read-only diagnosis,
  then retry a bounded number of materially different, non-destructive fixes. This
  includes a missing workspace setup, stale Git checkout, inactive user service,
  missing non-privileged environment variable or ordinary build error. Preserve
  the original error and do not hide it with unrelated workarounds.
- Level 3 — user-owned authority or physical action: ask the user immediately.
  This includes `sudo` or password prompts, installing system packages, changing
  udev/system network/device configuration, rebooting or power-cycling equipment,
  enabling physical motion, manipulating cables/E-stop, entering credentials, or
  any destructive or difficult-to-reverse operation.
- Escalate a Level 1/2 problem to the user only after the same blocker remains
  after reasonable diagnosis, or when the next useful step crosses into Level 3.
  Report the exact command, error, attempted fixes and the single requested action.
- Treat failures as project knowledge. When a problem is recurring and its fix is
  generalizable, update this file or the relevant runbook in the same coherent
  change so future agents use the proven command pattern. Do not document one-off
  noise that has no reusable lesson.

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
- UWB remains code-preserved and shadow-only when explicitly enabled, but it is
  hidden and excluded from the current MVP.
- Orin exclusively owns the SO-101, both RGB cameras and VL53L0X serial ports.
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
- An archived worktree with initialized submodules may reject ordinary
  `git worktree remove` even when clean. Use `--force` only after all three checks
  pass: the resolved path is the intended archived worktree, `git status --porcelain`
  is empty, and the branch tip exactly matches its pushed `archive/*` tag.
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
