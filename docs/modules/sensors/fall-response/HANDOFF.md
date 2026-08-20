# Fall Response Backend And Console Handoff

Verification date: 2026-08-20
Canonical implementation commit: `36c19c0`

## Current boundary

The fall-response subsystem now has two explicitly selectable scan modes:

- `static`: the proven no-motion fallback. It captures the stationary front
  camera for twelve virtual headings and keeps all model, VLM, cancellation and
  notification behavior available.
- `nav2_spin`: a real, fail-closed `nav2_msgs/action/Spin` adapter. It never
  publishes `/cmd_vel`, never starts Nav2 by itself and moves only after a fall
  event is accepted and every preflight gate passes.

Starting `project-link-emergency.target` starts the front camera, authenticated
HTTP gateway, visual coordinator and WeChat notifier. It does not start the
base, lidar, mapping or Nav2 and it never sends a Spin goal during startup.

The Ubuntu console has a dedicated **跌倒检测** page. Ubuntu renders video,
evidence, status, event history and configuration; Orin retains camera, model,
SQLite, Nav2 Action and notification ownership.

The backend can be started independently of the console over SSH:

```bash
ssh orin /home/wte/wheeltec_robot/scripts/standalone/start_fall_response.sh
```

This starts the fixed `project-link-emergency.target` allowlist and waits for the
front camera, fall coordinator and WeChat notifier through systemd state. It does
not create an event, send a notification or start motion.

## Implemented backend

Android endpoints remain:

```text
GET  /health
POST /api/fall
GET  /api/fall/{event_id}
POST /api/fall/{event_id}/cancel
```

All require `X-Fall-Guard-Token`. The public states remain `accepted`,
`scanning`, `verifying`, `notified`, `not_fall`, `cancelled` and `failed`.
The SQLite store now enforces an explicit state-transition allowlist in addition
to terminal-state immutability, idempotency and atomic notification claiming.

Runtime state defaults to:

```text
~/.local/state/project-link/fall-response/events.sqlite3
~/.config/project_link/fall_response.env
~/.config/project_link/fall_response.yaml
~/.config/project_link/wechatbot/credentials.json
~/.local/state/project-link/clawbot/binding.json
~/.local/state/project-link/clawbot/notifications.sqlite3
/home/wte/models/project_link/human-fall-detection-yolo11.pt
/home/wte/models/yolov8s-worldv2.pt
```

Credentials and databases remain mode `0600`. The CUDA model environment remains
isolated at `~/.local/share/project-link/venvs/fall-cuda`; it does not replace
LeRobot's Python/Torch environment.

## Typed operator interface

The Orin publishes:

- `/fall_detection/status` (`FallResponseStatus`): scan mode, active event,
  stage/progress, headings, confidence and every readiness/safety gate.
- `/fall_detection/evidence/compressed`: the latest bounded candidate or
  notification JPEG; evidence is not written to disk by the GUI.

It exposes:

- `/fall_detection/list_events`
- `/fall_detection/get_event`
- `/fall_detection/cancel_active`
- `/fall_detection/create_demo_event`
- `/fall_detection/run_preflight`

The event query returns typed event records and their SQLite transition timeline.
The console agent separately allowlists start/stop/restart of the emergency
target and restart of the WeChat service. No GUI field accepts arbitrary unit
names or shell commands.

The full demo-event button is deliberately guarded by a warning: in
`nav2_spin` it can rotate the robot, and with real-contact notification enabled
it can send a real message.

## Visual and notification path

- `/dev/project_link_front_camera` remains the sole chassis-camera device.
- `/front_camera/capture_still` supplies cached full-resolution JPEG frames.
- Primary model: YOLO11 `fallen/sitting/standing`.
- YOLO-World only establishes person presence and never confirms a fall.
- Strong candidates interrupt a live Spin segment, return to the candidate and
  capture two recheck frames.
- Weak candidates and World fallback retain the previous multi-image VLM path.
- VLM rejection restores the initial heading; a confirmed candidate leaves the
  camera facing the person.
- The WeChat notifier exits non-zero if its long-poll loop dies so
  `Restart=on-failure` can recover it. The page shows `notification_ready` and
  has an allowlisted restart button.

## Configuration

Non-secret fall parameters are stored in
`~/.config/project_link/fall_response.yaml` and edited through the fixed SSH
configuration helper. Simple mode exposes scan mode, real-contact notification,
segment count, frames per heading and clearance radius. Advanced mode exposes
bounded model thresholds, recheck, costmap, stop and Spin timeout parameters.

Secrets remain in `fall_response.env`; the helper masks Token/API Key values and
never returns them through ROS. Saving does not restart services. Use the page's
**重启并读取配置** button after saving.

## Safety contract

`nav2_spin` preflight requires:

- `/spin` ready and required Nav2 lifecycle nodes active;
- fresh `odom -> base_footprint` TF and `/odom`;
- fresh local costmap and no high-cost cell inside the configured rotation sweep;
- `/cmd_vel` publishers limited to the allowlisted Nav2 path;
- competing NavigateToPose/NavigateThroughPoses goals cancelled and the robot
  stably below `0.03 rad/s` for `250 ms`;
- the manipulator service inactive or its torque disabled;
- the fall event still active and not cancelled.

Any failed gate degrades to a visual-unconfirmed alert. Direct velocity fallback
is forbidden. Cancellation always requests Spin cancellation, waits for Nav2
acknowledgement and waits for stable zero angular velocity before completing.

## Verified deployment state

At `36c19c0`, normal Linux builds completed on both hosts. Orin focused tests
passed `86` cases and Ubuntu console tests passed `44` cases. The new user units
passed static verification, the typed status/evidence/event/configuration
interfaces were discovered over domain 42, SQLite history queries succeeded and
the Ubuntu page passed an offscreen simple/advanced-mode render check.

The installed Orin runtime is active in `scan_mode=static`. It reports the front
camera, both CUDA models, VLM and WeChat notifier ready; static preflight returns
success without requiring Nav2. The validation created no event, sent no real
notification, started no Nav2 stack and published no `/cmd_vel`.

## Remaining verification gates

1. Keep `scan_mode=static`; run a phone and console demo event with real-contact
   notification disabled.
2. Start the already-validated Nav2 stack and run the read-only Nav2 preflight.
3. With the area clear, arm stowed/torque off and physical E-stop ready, follow
   `NAV2_ASYNC_SCAN_HANDOFF.md` for supervised motion gates.
4. Keep `static` as the production default until two full supervised Spin cycles pass.
