# Fall Response Nav2 Spin Validation Handoff

Verification date: 2026-08-20
Canonical implementation commit: `36c19c0`; physical validation has not started

## Implementation status

The real adapter is implemented in
`project_link_fall_response/nav2_spin.py`. It uses only
`nav2_msgs/action/Spin`; the package contains no Twist publisher.

`AsyncScanOrchestrator` now accepts a cooperative `move_to_heading(...)`
callback. A live Spin wait repeatedly polls completed CUDA inference. When a
strong earlier result appears it:

```text
requests Spin cancellation
  -> waits for cancellation acknowledgement
  -> waits for |odom.angular.z| <= 0.03 rad/s for 250 ms
  -> uses actual odometry heading after the partial turn
  -> returns to the candidate heading
  -> captures two recheck frames
```

If recheck fails, the interrupted segment is resumed. Delayed inference from a
rejected heading cannot win again. Phone/console cancellation invalidates the
event and cancels the active Spin.

The scan captures `0, 30, ... 330°`, then completes the last `330 -> 360°`
segment while the final inference jobs run. Thus a full circle uses twelve
physical 30-degree segments while retaining twelve camera headings.

## Preserved decision contract

- Primary labels: `fallen`, `sitting`, `standing`.
- Strong threshold: `0.60`.
- Weak full-scan threshold: `0.25`.
- Recheck: at least one frame `>=0.55` and average `>=0.50`.
- YOLO-World person threshold: `0.50`.
- Full World coverage is required when the specialized model has no credible candidate.
- VLM rejection returns to the initial heading.
- VLM confirmation leaves the camera at the candidate heading.
- No-person/model/Nav2 failure is degraded, never automatically `not_fall`.

## Read-only preflight

The console's **只读运行 Nav2 预检** calls
`/fall_detection/run_preflight`. It sends no action goal and does not cancel an
existing navigation goal. It checks current readiness and publishes every gate
on `/fall_detection/status`.

The event-time preflight additionally cancels allowlisted competing navigation
actions and confirms stable zero angular velocity before the first Spin.

## Default and notification gates

`scan_mode` remains `static` by default. Select `nav2_spin` through the fall page
and restart the fall service only when supervised motion validation is planned.

Keep `notification_enabled=false` for the first motion tests. In this mode the
assessment finishes without contacting the bound person and the event ends as a
clearly messaged `failed/notification_suppressed` terminal result; this preserves
the Android public status enum and prevents accidental real alerts.

The `static` deployment path is Linux-verified at `36c19c0`: Orin passed `86`
focused tests, Ubuntu passed `44` console tests, typed DDS interfaces and event
history were observed, and static preflight succeeded with zero `/cmd_vel`
publishers. This does not validate any physical Spin behavior.

## Mandatory physical sequence

Before every motion test the user must clear the full rotation area, stow the
arm and disable torque, keep the physical E-stop/power cut available and remain
beside the robot.

1. Start the already-validated Point-LIO/Nav2 stack; do not start GUI teleop.
2. Run read-only preflight and require every page gate green.
3. Disable real-contact notification.
4. Execute one supervised 30-degree segment.
5. Cancel during a segment; verify Nav2 acknowledgement and physical stop.
6. Execute a 90-degree candidate-return/recheck test.
7. Execute a 180-degree scan.
8. Execute a full 360-degree empty-area scan.
9. Regress standing, sitting and fallen scenes.
10. Pass two complete supervised Spin cycles before enabling real notification.

Do not add direct `/cmd_vel`, blind timeout expansion, obstacle bypass or a
motion fallback to make a failed gate pass.
