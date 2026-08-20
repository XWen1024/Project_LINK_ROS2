# Ubuntu Control Console Architecture

Status: accepted implementation architecture
Last reviewed: 2026-08-20
Target: Ubuntu 22.04 + ROS 2 Humble

## Boundary

- Ubuntu performs all GUI, chart, video, map, costmap and optional RViz2 rendering.
- Orin remains headless and exclusively owns robot hardware and control services.
- The console is a practical toolbox using restrained native Qt controls, not a
  decorative or science-fiction dashboard.

## Components

- `project_link_console_interfaces`: typed state, lifecycle, event and teleop interfaces.
- `project_link_console_agent`: Orin systemd control, health aggregation, log
  forwarding, configuration validation and teleop watchdog.
- `project_link_console_gui`: Ubuntu PySide6 application.
- `deploy/systemd/user`: versioned user services and mapping/navigation targets.

The interface, agent, systemd foundation and all planned PySide6 pages are
implemented in the repository. UWB remains available behind an explicit opt-in
environment flag but is hidden for the current MVP. The GUI includes a hardware-free demo mode and a
repository-owned RViz2 profile. The user-unit graph is documented in
`deploy/systemd/README.md`; it remains behind the existing script fallback until
two supervised Orin validation cycles pass.

Ubuntu validation uses `/home/xwen/wheeltec_robot` and pinned user-local PySide6
from `src/project_link_console_gui/requirements-ubuntu.txt`. ROS 2 Humble remains
system-installed; the GUI dependency is not represented as a Jammy apt rosdep key.

The MVP GUI subscribes to ROS maps, costmaps, scans, paths, images and module
status through native DDS Peer on domain 42. Process lifecycle is requested
through the console agent, which controls an allowlisted set of `systemd --user`
units. Configuration and secrets use SSH. The preserved DDS Router domain-142/42
path is experimental after its 2026-08-19 typed endpoint field gate failed.
Secrets are edited through
an SSH-invoked allowlisted configuration helper and are never transported as ROS
messages.

Every production ROS process sources `scripts/project_link_dds_profile.sh`. It
asks the kernel which interface routes to the configured peer/default gateway,
generates a user-runtime Fast DDS XML profile, disables builtin transports and
re-enables only an explicit SHM transport plus one UDP transport whitelisted to
that IPv4 address. SHM preserves same-host systemd readiness probes without
advertising another network interface. This avoids duplicate DDS traffic when a host
has Ethernet and Wi-Fi on the same subnet while still following DHCP/network
changes. The vehicle baseline is Orin-to-CPE Ethernet plus Ubuntu-to-CPE 5 GHz
Wi-Fi; lifecycle/configuration remain separate SSH traffic.

The 2026-08-20 temporary router passed ICMP/TCP but dropped bidirectional unicast
UDP between Orin Ethernet and Ubuntu Wi-Fi. In that topology Orin must use the
allowlisted `PROJECT_LINK_DDS_INTERFACE=wlP1p1s0` override so both DDS peers stay
on the reachable Wi-Fi segment. This is still single-interface DDS, not a return
to duplicate multi-NIC advertisement. Remove the override only after the vehicle
CPE passes wired-to-wireless UDP and multicast validation.

The helper is `scripts/project_link_console_config.py`. It accepts only the
`voice`, `global`, `uwb` and `fall` sections, validates allowlisted fields, writes local
mode-0600 files atomically, never returns secret values, and never accepts a
shell command or arbitrary path. The Ubuntu GUI sends JSON through SSH stdin.
Ubuntu needs its own authorized SSH key; Windows private keys must not be copied.

## Pages

1. Navigation and mapping: system mode, 2D layers, goals, map saving, health and
   supervised dead-man teleoperation.
2. Manipulation: independent native-MJPEG arm-camera video, isolated CUDA
   YOLO-World state/timing, click-selected yellow servo target, configurable
   green detection-box anchor, SO-101, presets, calibration, ToF and visual grasp.
3. Voice: classic/Qwen exclusive selection, session state, simplified events and
   per-stage timing.
4. Voice configuration: common parameters, prompt profiles and registered tools.
5. Fall response: service/model/VLM/WeChat/Nav2 readiness, live camera and
   bounded evidence, current event progress, typed SQLite timeline, cancellation,
   read-only Spin preflight and bounded simple/advanced configuration.
6. UWB: code-preserved shadow tooling, hidden by default and outside the current MVP.
7. Global settings: devices, paths, ROS networking and masked API credentials.

Simple mode exposes only common operator controls. Advanced mode exposes the
full categorized parameter catalog with Chinese labels, units, limits, defaults,
restart requirements and safety notes.

## Visualization And Teleoperation

The built-in view is a Qt 2D renderer for occupancy grids, global/local costmaps,
LaserScan, downsampled XY point-cloud projection, paths, footprint and targets.
Complex 3D point clouds and TF debugging open a repository-owned RViz2 profile
in a separate process.

Map, costmap, scan, path, front-camera and fall-evidence subscriptions exist only
while their owning page is visible. The front-camera JPEG is decoded once by the
visible consumer. Occupancy grids retain the ROS signed-byte buffer and use a Qt
indexed palette instead of a Python per-pixel RGBA loop. Production preview is
native 1280x720 capture with a stable 24 FPS publish gate; advanced mode may use
30 FPS after bandwidth validation.

The Ubuntu GUI never publishes `/cmd_vel` directly. It sends bounded teleop
requests to the Orin agent. The agent publishes only in mapping mode and stops
within 250 ms when the dead-man key, GUI focus, heartbeat, ROS connection or
mode gate is lost. Starting Nav2 disables teleoperation before activation.

The Ubuntu sidebar can report tunnel/router state when experimental mode is
explicitly enabled. `deploy/dds-router/bin/project-link-console` now defaults to
domain 42 and starts no Router service. Setting
`PROJECT_LINK_ENABLE_EXPERIMENTAL_DDS_ROUTER=1` restores the domain-142 launcher
for diagnostics only; it remains blocked from production use until a new
end-to-end Topic/Service/Action gate passes.
