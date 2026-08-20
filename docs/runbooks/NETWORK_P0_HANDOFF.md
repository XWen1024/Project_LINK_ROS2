# Network P0 And Console Stability Handoff

Status: superseded in part by live validation on 2026-08-21
Last updated: 2026-08-21
Canonical branch: `main`
Canonical commit: `59b5204`

## 1. Historical Stop Point

The user requested a temporary stop and handoff at this point. Work was later
explicitly resumed on 2026-08-21; the restriction below is historical rather
than a current instruction.

No router setting was saved or applied. The router was not rebooted. No Nav2
goal, `/cmd_vel`, mechanical-arm motion or fall-response event was triggered.

## 2. Changes Completed And Pushed

The following commits were made directly on `main` and pushed to GitHub:

- `2e5bda9 fix: stabilize console network and lifecycle queues`
- `5d0bb6e fix: add unicast peer discovery to Fast DDS`
- `2281735 chore: mark DDS profile helper executable`
- `b831535 fix: use Humble Fast DDS initial peer schema`
- `f545d89 fix: support single-interface DDS on split LANs`
- `59b5204 fix: preserve local DDS discovery with SHM`

The experimental initial-peer XML was removed after packet tests showed that it
could not bypass the actual connectivity failure. The final profile contains:

- one explicitly whitelisted UDPv4 address;
- an explicit SHM transport for same-host ROS discovery and systemd readiness;
- builtin transports disabled, so Fast DDS cannot advertise every host NIC;
- optional `PROJECT_LINK_DDS_INTERFACE` and `PROJECT_LINK_DDS_PEER_IP` overrides.

Other completed console changes:

- heavy map, scan, path, camera and fall-evidence subscriptions now follow the
  visible page and are destroyed while hidden;
- the same front-camera JPEG is routed only to the visible consumer;
- the fall page no longer creates a request-on-result self-triggering loop;
- the GUI command queue is bounded, prioritized and coalesces background work;
- voice switching is single-flight and submits nonblocking systemd jobs;
- voice buttons lock during a lifecycle request and unlock on completion;
- map rendering uses the ROS signed-byte buffer and a Qt indexed palette instead
  of a Python per-pixel RGBA loop;
- the stable front-camera DDS preview default is now 1280x720 at 24 FPS;
- `AGENTS.md` and architecture/navigation documentation record the new rules.

The pre-existing uncommitted mechanical-arm recording work was deliberately not
included in these commits. Preserve it when resuming:

```text
src/project_link_visual_grasp/project_link_visual_grasp/core.py
src/project_link_visual_grasp/project_link_visual_grasp/node.py
src/project_link_visual_grasp_gui/project_link_visual_grasp_gui/app.py
src/wheeltec_robot_msg/CMakeLists.txt
src/wheeltec_robot_msg/msg/VisualGraspStatus.msg
src/project_link_visual_grasp/project_link_visual_grasp/joints.py
src/project_link_visual_grasp/project_link_visual_grasp/recorded_motion.py
src/project_link_visual_grasp/test/test_recorded_motion.py
src/wheeltec_robot_msg/action/PlayRecordedMotion.action
src/wheeltec_robot_msg/srv/ManageRecordedMotion.srv
```

## 3. Linux Verification Completed

Both Linux repositories were fast-forwarded from GitHub during this session.

Orin:

- `project_link_console_agent` built successfully;
- console-agent tests: `38 passed`;
- versioned systemd units passed `systemd-analyze --user verify`;
- `project-link-console-agent.service` was active on the final SHM profile;
- its `ExecStartPost` local `/project_link/console/system_state` readiness probe
  passed after explicit SHM was restored;
- front-camera configuration was set through the allowlisted helper to 24 FPS;
- the console agent and front-camera service were the only services intentionally
  restarted during the network deployment; no motion service was commanded.

Ubuntu laptop:

- `project_link_console_gui` built successfully;
- focused console tests: `38 passed`;
- the offscreen demo created a Qt window without traceback; its command ended by
  the intentional timeout because the Qt event loop remains active;
- after the hidden-page and map changes, a short live GUI sample fell from the
  earlier roughly 68% CPU state to roughly 9% CPU. This sample occurred while
  cross-host DDS was still unavailable and is not a final loaded benchmark.

## 4. Current Host And Network Facts

Known addresses during this handoff:

```text
NRadio router:       192.168.66.1
Orin Wi-Fi:          192.168.66.27  (wlP1p1s0)
Orin Ethernet:       192.168.66.52  (enP8p1s0)
Ubuntu laptop Wi-Fi: 192.168.66.160 (wlo1)
Windows/Mihomo:      192.168.66.79:7897
```

The Orin has Wi-Fi and Ethernet in the same `192.168.66.0/24` subnet. Its normal
route to the laptop prefers Ethernet. A source-address whitelist does not by
itself guarantee the Linux kernel will transmit through the matching physical
device when two same-subnet interfaces exist. This same-subnet dual-homing must
be eliminated or handled with verified policy routing; it is not a robust
production topology.

The Orin allowlisted runtime configuration was set to:

```text
PROJECT_LINK_DDS_INTERFACE=wlP1p1s0
PROJECT_LINK_DDS_PEER_IP=192.168.66.160
FRONT_CAMERA_PREVIEW_FPS=24.0
```

This was a temporary diagnostic override. `PROJECT_LINK_DDS_PEER_IP` is only a
route-selection hint in the final helper; it is not a Fast DDS discovery server
or unicast peer list. Do not treat the override as a proven fix.

## 5. Earlier Cross-Host DDS Diagnosis Was Superseded

The earlier conclusion that all cross-host DDS was blocked was disproved by a
live Ubuntu desktop screenshot on 2026-08-21. The console showed `Orin 已连接`
and continuously rendered the occupancy map, laser scan and 1280x720 camera at
about 23.8 FPS. Treat the generic socket/CLI tests below as conflicting evidence,
not as the current system conclusion.

The actual remaining split is narrower: sensor topics are reaching Ubuntu, but
Nav2 was stopped and the console lifecycle Action path could not restart it.
Therefore the robot could not accept navigation goals. Standalone SSH/systemd
start scripts were added to bypass the console lifecycle path.

Evidence collected:

- ordinary ICMP ping was bidirectional with no packet loss and low latency;
- ARP/neighbor tables resolved the real peer MAC addresses, not the gateway MAC;
- standard Python UDP socket tests received no payload in either direction:
  - Orin Wi-Fi `.27` to Ubuntu `.160`;
  - Orin Ethernet `.52` to Ubuntu `.160`;
  - Ubuntu `.160` to Orin Ethernet `.52`;
- high-port TCP connection tests were also refused in both directions even while
  the temporary listener was confirmed by `ss`;
- SSH from the Windows development host to both machines continued to work, but
  that does not validate new laptop-to-Orin LAN connections.

Because high-port TCP and UDP both failed, this is not specifically a Fast DDS
schema or multicast problem. It is a general peer-to-peer traffic gate or host
firewall issue.

## 6. Read-Only NRadio Router Audit

Router identity reported by the UI:

```text
Model: C2000-518
Firmware: NROS-1.9.6.n1.c5
UI/base MAC: FC:83:C6:15:77:8C
```

The router was inspected through its authenticated LuCI pages in read-only mode.
Credentials are intentionally not recorded here.

Observed configuration:

- `Isolate Clients` was unchecked for the displayed 2.4 GHz and 5 GHz SSIDs;
- wireless MAC ACL policy was `Disable`;
- global Access Control returned `disabled=1` with empty allow/deny lists;
- Client QoS was unchecked;
- UPnP was unchecked;
- DMZ was unchecked;
- port-forwarding rule count was zero;
- the system log explicitly reported `wlan1 ap_isolate:0`;
- no DROP/REJECT firewall log explaining the tests was found.

The online-client API showed all relevant devices under the same router:

```text
Ubuntu laptop Wi-Fi  10:5F:AD:95:DC:DD  192.168.66.160  WenHome_5G
Orin Wi-Fi           9C:C7:D3:F6:DE:6D  192.168.66.27   WenHome_5G
Orin Ethernet        4C:BB:47:32:EA:4E  192.168.66.52   wired
Windows Wi-Fi        04:7B:CB:2E:0D:72  192.168.66.79   WenHome_5G
```

The actual Wi-Fi BSSID seen by both Linux clients was
`24:EF:B4:52:16:21`. The NRadio API associated them with its own AP/device ID,
so the differing BSSID appears to be an internal radio/BSSID identity rather
than proof that they use a different router.

Conclusion: the visible NRadio settings do not show an enabled client-isolation
or ACL option. Do not tell the user simply to turn off `Isolate Clients`; it is
already off and the driver log agrees. The remaining cause could be:

1. a host firewall/ruleset rejecting new peer connections;
2. NROS firmware or hardware-offload behavior not represented by the visible UI;
3. the unsupported same-subnet dual-NIC topology on Orin;
4. a combination of the above.

## 7. Required Next Checks

These checks require a new explicit user request. The firewall commands require
sudo and should be run by the user according to `AGENTS.md`.

First inspect both hosts, especially the Ubuntu laptop:

```bash
sudo ufw status verbose
sudo nft list ruleset
sudo iptables -S
sudo iptables -t raw -S
```

Relevant partial evidence already available:

- Orin: `ufw` command is not installed; `firewalld` was inactive.
- Ubuntu: `ufw.service` appeared active, while `/etc/ufw/ufw.conf` contained
  `ENABLED=no`; only root can reveal the effective nftables/iptables rules.
- `firewall-cmd` is not installed on Ubuntu, so firewalld is not the active tool.

After recording the rules, temporarily allow a bounded diagnostic range from
the robot subnet, or temporarily disable the effective host firewall under user
supervision, then repeat one TCP and one UDP socket gate. Do not change the
router until host firewalls have been conclusively excluded.

If host firewalls are clean and the peer tests still fail:

1. export/back up the NRadio configuration;
2. check for a newer stable firmware than `NROS-1.9.6.n1.c5`;
3. toggle `Isolate Clients` on, apply, then off and apply to force the driver
   state to be rewritten, followed by a supervised router reboot;
4. repeat wired-to-wireless and wireless-to-wireless TCP/UDP gates;
5. only accept Orin-Ethernet plus Ubuntu-5GHz as production after unicast UDP and
   multicast both pass.

For the Orin, avoid leaving Ethernet and Wi-Fi active in the same subnet during
the final gate. Prefer the intended production arrangement:

```text
Orin: Ethernet only to the vehicle CPE
Ubuntu: 5 GHz Wi-Fi only to the same CPE
```

Alternatively, use distinct subnets plus explicit routing. Do not rely on two
same-prefix default interfaces and source-address binding alone.

## 8. Work Not Started In This P0

The following planned work remains untouched:

- `RelativeMotion.action`;
- the unified `/cmd_vel` speed arbiter;
- `move_forward`, `move_backward`, `turn_left`, `turn_right` tools;
- Qwen and classic voice integration for those relative-motion tools;
- full 20-cycle voice lifecycle and bandwidth/CPU field acceptance;
- production H.264/GStreamer video transport;
- restart of the active Nav2/Point-LIO/lidar/base stack to adopt the new DDS
  profile. These services were intentionally left running to avoid disrupting
  the robot while the earlier network diagnosis was unresolved. Live validation
  has since shown that sensor DDS is operational, while the console lifecycle
  path for restarting Nav2 remains the actual problem.

## 9. Resume Order

When the user explicitly resumes work:

1. read this handoff and `AGENTS.md`;
2. run the sudo firewall audit supplied by the user;
3. fix and repeat generic TCP/UDP peer tests before testing ROS;
4. remove same-subnet dual-homing or establish verified policy routing;
5. validate `/project_link/console/system_state` cross-host discovery;
6. start the console and measure traffic/CPU with only one DDS UDP interface;
7. run 20 voice start/stop cycles and verify no queue overflow;
8. only then implement the relative-motion/action/arbiter P1.
