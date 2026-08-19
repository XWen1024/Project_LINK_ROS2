# Project LINK Progress

Last updated: 2026-08-20
Canonical branch: `main`

## Current Milestone

Build a single Ubuntu 22.04 control console that replaces routine startup scripts,
renders navigation and module state on the laptop, and controls headless Orin
services through typed ROS interfaces and `systemd --user`.

The current MVP uses verified native DDS Peer on domain 42 plus SSH-owned
lifecycle/configuration, followed by one repeatable Qwen Realtime to Nav2 loop.
UWB is excluded. The STM32-native handset is the primary manual driving control;
GUI teleop remains an advanced-mode backup.

## Repository Consolidation Completed

- [x] Split the former 50-file dirty main into coherent VL53L0X, visual-grasp,
  Windows-lab, voice-documentation and project-status commits.
- [x] Merge Qwen3.5 Omni Realtime into `main`.
- [x] Preserve old branch tips with annotated archive tags.
- [x] Delete local and remote Qwen, Volc S2S and obsolete SLAM development branches.
- [x] Remove obsolete worktrees; `main` is the only active branch/worktree.
- [x] Reorganize documentation into architecture, module, runbook and archive directories.
- [x] Add current manipulation, classic voice and Qwen Realtime handoffs.

Archive tags:

- `archive/qwen-realtime-premerge-20260814`
- `archive/volc-s2s-smoke-20260812`
- `archive/volc-s2s-smoke-orin-final-20260815`
- `archive/volc-s2s-integration-20260812`
- `archive/slam-migration-20260627`

## Verified Software State

- Visual-grasp direct regression: 52 scenarios passed.
- VL53L0X protocol valid/invalid frame checks passed.
- Qwen Realtime direct regression: 11 scenarios passed.
- Python syntax checks passed for visual grasp, VL53L0X, Windows lab and Qwen packages.
- PowerShell parsing passed for the Windows visual-grasp launcher.
- Qwen and classic voice are both present on `main` but remain mutually exclusive at runtime.
- The Android fall-guard client and Orin backend include the verified static
  fallback plus a newly implemented fail-closed Nav2 Spin adapter. The backend
  now enforces strict SQLite transitions, supports segment-interrupting CUDA
  results, typed status/event/timeline/evidence interfaces and a dedicated
  console page. Models, VLM and real WeChat delivery were previously verified;
  the new interfaces/Nav2 adapter still require Linux build and supervised gates.
- Orin direct pytest across console agent, classic voice and Qwen Realtime:
  79 passed after the new event/config integration.
- The versioned systemd graph contains 13 services, 3 mode targets and 1 shared
  platform target with no missing Project LINK unit references.
- Console GUI model, configuration-helper, page, embedding and safety checks:
  16 passed on Ubuntu. Python syntax, package XML and all four new page renders pass.
- Orin validation on 2026-08-15: console interfaces and agent built successfully;
  the 17-unit systemd graph passed `systemd-analyze --user verify`; the installer
  started only the console agent; `/project_link/console/system_state` reported
  mode `off`, no teleop and all hardware/navigation units inactive.
- Orin repository consolidation was repeated after field validation: archived
  Volc worktrees were removed, their unique tips are pushed as archive tags, and
  `main` is now the only local branch/worktree. Runtime `.data` and `.posegraph`
  files were preserved untracked.
- Ubuntu laptop validation found Ubuntu 22.04.5 x86_64 and ROS 2 Humble Desktop.
  Git, colcon and Navigation2 prerequisites were installed; PySide6 6.11.1 was
  installed user-local; console interfaces/GUI built successfully; offline demo
  and the real ROS bridge passed offscreen smoke tests. Ubuntu received the Orin
  mode-`off` system state over domain 42. A visible read-only console is active in
  the local `xwen` desktop session.
- The embedded mechanical-arm page was visually and offscreen validated on Ubuntu
  at `60e1b74`. Its Humble-incompatible `rclpy.parameter_client` import was replaced
  with standard parameter services; initialization errors now survive page
  construction and reach the console log. Three rapid service restarts completed
  without traceback or timeout (`0.80 s`, `0.02 s`, `0.02 s`).
- Voice control, voice configuration, UWB shadow and global-settings pages were
  implemented and visually validated on Ubuntu at `dbc31a7`. The Orin agent now
  forwards sanitized timing phases, exposes only UWB shadow start/stop services,
  and keeps classic/Qwen mutually exclusive. Both voice services and UWB remained
  inactive throughout validation.
- The allowlisted configuration helper passed masking and runtime-YAML tests.
  Ubuntu now has its own ED25519 key authorized exactly once on Orin; real config
  read, secret masking and no-op write passed without copying the Windows key.
- After the LAN changed on 2026-08-19, bounded subnet discovery found and verified
  Ubuntu at `10.255.176.106` and Orin at `10.255.176.119` using hostname,
  machine-id and ED25519 host-key fingerprints. Windows aliases now use those
  numeric IPs directly; the attempted permanent mDNS proxy mapping was removed.
  Future alias failures trigger immediate rediscovery and verified IP refresh.
- The console window now ignores hidden-page minimum sizes, bounds its initial
  geometry to the Ubuntu desktop, reduces the embedded manipulation minimums and
  provides a manually collapsible icon sidebar. Real mode also uses a local lock
  to prevent duplicate GUI/ROS nodes. The voice page now exposes automatic/manual
  control-Action discovery and shows start/switch results directly on the page.
- Hardware enumeration on 2026-08-19 separated the icSpring chassis-front camera
  from the Generic/Realtek arm camera. C63A frames were confirmed at 115200 on
  serial `5B1F024697`. Exact serial udev rules are installed and verified for all
  six production aliases; the unsafe broad chassis rule was backed up and disabled.
- No-motion hardware validation passed: C63A `/odom` and IMU were about `20 Hz`,
  voltage was `23.98 V`, `/cmd_vel` had zero publishers, Unitree L1 cloud was
  about `9.1-9.2 Hz`, L1 IMU about `238 Hz`, and the arm camera captured a frame.
  SO-101 was identity-checked only; no torque or motion command was sent.
- Unitree L1 three-axis installation calibration was completed from a stationary
  cloud using the physical constraints that raw forward maps to chassis `+X`
  and the measured floor lies at `z≈0`. The saved mount is roll `-90°`, pitch
  `-2.2°`, yaw `90°`; red production and green preview clouds overlap in RViz2.
  The mapping target, Point-LIO, `/odom_lio`, `/scan_accumulated` and `/map`
  returned ready without publishing velocity or sending a navigation goal.
- The Point-LIO Nav2 local/global inflation layers were reduced from
  `0.40 m / 3.5` to `0.28 m / 5.0` while retaining the measured
  `0.51 m x 0.41 m` footprint and `0.01 m` padding. Live testing opened the
  previously overlapping soft-cost corridor and completed multiple supervised
  goals. Remaining failures were tied to hard occupied cells or a goal outside
  the global costmap, not to the inflation radius alone.
- The front-camera path now uses native 1280x720 MJPEG pass-through at about
  `29.8 FPS`. Fixed exposure `300` and gain `32` preserve that rate; the navigation
  page exposes runtime automatic/manual exposure controls only in advanced mode.
  Orin agent tests passed (`28 passed`) and Ubuntu GUI tests passed (`25 passed`)
  at `main@3210e70`. A real Ubuntu RosBridge round trip changed the camera to
  automatic exposure, read it back, then restored manual `300/32`; `/cmd_vel`
  and all motion paths were untouched.
- The independent manipulator camera is now also configured for its verified
  native `1280x720@30 FPS` MJPEG mode instead of the previous YUYV 10-FPS path.
  Native JPEG bytes are forwarded without Orin re-encoding; CUDA detection
  decodes the newest frame only at its own submit rate.
- YOLO-World was separated from the LeRobot/SO-101 process. The new
  `project-link-visual-grasp-detector.service` uses the isolated `fall-cuda`
  site-packages with Torch `2.8.0`, CUDA `12.6`, Ultralytics `8.4.32` and a pinned
  Ultralytics CLIP commit. A live `1280x720` arm-camera frame completed CUDA
  inference on `cuda:0` in about `56.8 ms`; the validation called no arm service
  and sent no joint command.
- The Ubuntu mechanical-arm page restored the Windows-lab click-to-select visual
  servo flow. A yellow target cross and configurable green box anchor are visible;
  the operator can choose five anchors, reset/use the current detection point and
  start persistent visual servo separately from automatic grasp. Horizontal is
  the safe default; vertical correction remains explicit. Ubuntu passed 67 related
  tests plus a direct offscreen panel smoke test at `main@f712eb3`.
- DDS Router v2.2.0 and locked dependencies built successfully on both Orin ARM64
  and Ubuntu x86_64. BatchMode SSH, tunnel bootstrap, loopback-only listeners,
  single/reverse forwarding, exact ROS DDS types and forced reader/writer tests
  were exercised. No typed endpoint crossed the WAN participants reliably, so
  all Router/tunnel units remain disabled/inactive and the console launcher now
  defaults to native domain-42 DDS. Router mode requires explicit experimental
  opt-in; the post-MVP robust path is a typed allowlisted console bridge over SSH.

## Current Implementation Order

1. [x] Add `project_link_console_interfaces`.
2. [x] Add the `project_link_console_agent` lifecycle, typed state and fail-closed
       teleop foundation. Journal event streaming and configuration validation remain.
3. [x] Add versioned `systemd --user` component units and mapping/navigation targets.
       Linux syntax/unit verification passed; two supervised Orin cycles remain required.
4. [x] Keep current scripts as fallback wrappers during field validation.
5. [x] Create the PySide6 console shell and offline demo mode.
6. [x] Implement the first navigation/mapping visualization, goal control and
       safe teleop slice. Ubuntu rendering, ROS integration and field motion remain.
7. [x] Refactor and embed the existing Ubuntu manipulation client as a console
       page. Ubuntu rendering/initialization is verified; Orin service/video and
       supervised SO-101 field validation remain required.
8. [x] Normalize voice status/events and implement classic/Qwen switching.
9. [x] Add prompt/tool profiles and masked global configuration management.
       Cross-machine SSH authentication still needs one user bootstrap step.
10. [x] Add UWB shadow plots, tuning and proposed-calibration capture.
11. [x] Identify the fixed production hardware set: C63A, Unitree L1, SO-101,
        wake board, chassis-front icSpring camera and independent arm camera.
        Versioned exact-serial udev rules are installed and all aliases verified;
        the unsafe broad chassis rule is disabled.
12. [x] Hide UWB by default and add the Orin-owned front-camera preview path to
        the navigation page. Linux build, native 720p/30-FPS streaming and
        advanced-mode automatic/manual exposure control validation passed.
13. [x] Build the locked DDS Router on both hosts and evaluate the domain-42/142
        SSH-tunnel route. The field gate failed, so preserve it as experimental
        and default the MVP launcher back to verified native DDS Peer.
14. [ ] Save a real named waypoint and complete three Qwen voice-navigation runs.
15. [ ] With the arm supported and torque off, restart the paired visual-grasp
        services and validate native 30-FPS display, CUDA status and click-selected
        horizontal servo in the visible Ubuntu GUI. Validate vertical servo separately.
16. [ ] After the MVP loop, implement a typed allowlisted console bridge over SSH
        and validate reconnect/state resynchronization before replacing DDS Peer.
17. [ ] Build and validate the dedicated fall-response page and typed interfaces
        on Orin/Ubuntu in `static` mode, then execute the supervised 30°/cancel/
        90°/180°/360° Nav2 Spin gate with real-contact notification disabled.

## Existing Hardware Gates

- Point-LIO Phase B plus Nav2 needs the planned endurance runs and continued lag monitoring.
- UWB live motion remains blocked on measured, operator-approved calibration and fault tests.
- VL53L0X ROS package still needs Orin build, stable udev alias and mounted-sensor validation.
- Visual grasp needs a complete Orin-to-Ubuntu camera/SO-101/ToF field loop and more grasp demonstrations.
- Classic voice still needs valid Volcano ASR credentials or an explicit documented fallback state.
- Qwen Realtime still needs real Nav2, grasp and 20-cycle AEC/interruption validation.
- Fall-response Nav2 Spin is implemented but remains blocked on Orin/Ubuntu build,
  read-only preflight and the supervised physical sequence in its current handoff.

## Safety Acceptance For The Console

- Starting services sends no goal and no nonzero velocity.
- Teleop is mapping-only, dead-man controlled and stops within 300 ms on every loss path.
- Nav2 activation verifies teleop is disabled and `/cmd_vel` ownership is expected.
- Stale system, sensor and module state becomes visibly unavailable within two seconds.
- The Ubuntu GUI never directly owns robot hardware or secret files.

Historical project progress is preserved at
`docs/archive/handoffs/PROGRESS_DETAIL_20260815.md`.
