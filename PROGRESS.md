# Project LINK Progress

Last updated: 2026-08-15
Canonical branch: `main`

## Current Milestone

Build a single Ubuntu 22.04 control console that replaces routine startup scripts,
renders navigation and module state on the laptop, and controls headless Orin
services through typed ROS interfaces and `systemd --user`.

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
- `archive/volc-s2s-integration-20260812`
- `archive/slam-migration-20260627`

## Verified Software State

- Visual-grasp direct regression: 52 scenarios passed.
- VL53L0X protocol valid/invalid frame checks passed.
- Qwen Realtime direct regression: 11 scenarios passed.
- Python syntax checks passed for visual grasp, VL53L0X, Windows lab and Qwen packages.
- PowerShell parsing passed for the Windows visual-grasp launcher.
- Qwen and classic voice are both present on `main` but remain mutually exclusive at runtime.

## Current Implementation Order

1. [ ] Add `project_link_console_interfaces`.
2. [ ] Add `project_link_console_agent` with typed health, systemd control,
       journal events, configuration validation and teleop watchdog.
3. [ ] Add versioned `systemd --user` component units and mapping/navigation targets.
4. [ ] Keep current scripts as fallback wrappers during field validation.
5. [ ] Create the PySide6 console shell and offline demo mode.
6. [ ] Implement navigation/mapping visualization, goal control and safe teleop.
7. [ ] Integrate the existing Ubuntu manipulation client as a console page.
8. [ ] Normalize voice status/events and implement classic/Qwen switching.
9. [ ] Add prompt/tool profiles and masked global configuration management.
10. [ ] Add UWB shadow plots and proposed-calibration capture.

## Existing Hardware Gates

- Point-LIO Phase B plus Nav2 needs the planned endurance runs and continued lag monitoring.
- UWB live motion remains blocked on measured, operator-approved calibration and fault tests.
- VL53L0X ROS package still needs Orin build, stable udev alias and mounted-sensor validation.
- Visual grasp needs a complete Orin-to-Ubuntu camera/SO-101/ToF field loop and more grasp demonstrations.
- Classic voice still needs valid Volcano ASR credentials or an explicit documented fallback state.
- Qwen Realtime still needs real Nav2, grasp and 20-cycle AEC/interruption validation.

## Safety Acceptance For The Console

- Starting services sends no goal and no nonzero velocity.
- Teleop is mapping-only, dead-man controlled and stops within 300 ms on every loss path.
- Nav2 activation verifies teleop is disabled and `/cmd_vel` ownership is expected.
- Stale system, sensor and module state becomes visibly unavailable within two seconds.
- The Ubuntu GUI never directly owns robot hardware or secret files.

Historical project progress is preserved at
`docs/archive/handoffs/PROGRESS_DETAIL_20260815.md`.
