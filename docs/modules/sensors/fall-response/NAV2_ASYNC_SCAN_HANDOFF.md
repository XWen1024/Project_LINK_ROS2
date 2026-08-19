# Fall Response Async Scan to Nav2 Handoff

Verification date: 2026-08-20

## Current boundary

The fall backend contains a Nav2-independent asynchronous scan decision engine.
It simulates twelve headings at 30-degree intervals while continuing to use the
single stationary front-camera owner. It does not import `nav2_msgs`, create a
`Spin` Action client, publish `/cmd_vel`, or move the robot.

The simulation validates the concurrency and decision contract before physical
rotation is authorized:

```text
virtual heading reached
  -> capture three stable frames
  -> enqueue specialized fall inference
  -> continue to the next virtual heading without waiting
  -> high-confidence result interrupts the virtual scan
  -> return to the candidate heading is simulated
  -> capture two recheck frames
  -> reproduced candidate goes immediately to the VLM
```

If the full virtual circle produces no credible `fallen` candidate, YOLO-World
checks one overview frame from every heading. If people are present, all twelve
labeled views are submitted to the VLM. If neither model can establish that a
person is visible, the result is a degraded alert, never `not_fall`.

## Model contract

Production no longer uses YOLO Pose.

- Primary model:
  `/home/wte/models/project_link/human-fall-detection-yolo11.pt`
- Labels: `fallen`, `sitting`, `standing`
- SHA-256:
  `3f56ad30358d5c63bf8dbc0c1299cf68818c3d291dfb10c94107b94110aadd4c`
- License: AGPL-3.0. Retain VLM confirmation for alerts.
- Fallback: `/home/wte/models/yolov8s-worldv2.pt`
- Fallback SHA-256:
  `9b2c17ab6124a913e9b3a5c170617920d91b0f01111a8479da69f00e2cf27792`
- YOLO-World only establishes `person` presence. It never confirms a fall.

Default thresholds:

```text
specialized inference floor: 0.05
strong fallen candidate: 0.60
weak fallen candidate: 0.25
recheck single-frame threshold: 0.55
recheck average threshold: 0.50
YOLO-World person threshold: 0.50
```

Install both models explicitly with:

```bash
deploy/systemd/bin/project-link-install-fall-models
```

The installer validates fixed hashes before atomic replacement. Runtime services
never download models.

## Interface to replace with Nav2

`AsyncScanOrchestrator` in `project_link_fall_response/async_scan.py` owns the
model-independent decisions. Its `capture` callback receives:

```text
angle_deg
frame_count
stage
scan_step
scan_total
```

The coordinator currently treats the angle as virtual and immediately captures
from the stationary front camera. Nav2 integration must replace only the
heading-arrival implementation around this callback. Inference, recheck
thresholds, World fallback, VLM selection and notification claiming remain
unchanged.

The simulator uses a configurable one-second virtual travel/settle interval by
default. This is intentionally long enough for CUDA inference to complete and
exercise early interruption; it is not a Nav2 speed command.
The specialized model is warmed once before accepting scan work so the first
real event does not pay model-load latency during the circle.

Replay one still image without ROS motion:

```bash
ros2 run project_link_fall_response simulate_async_scan --image /path/test.jpg
```

Replay a real offline circle with `--angles-dir <directory>` containing:

```text
0.jpg  30.jpg  60.jpg  ...  330.jpg
```

## Required Nav2 adapter

Add a separate adapter with these operations:

```text
preflight()
go_to_relative_heading(+30 degrees)
cancel_current_segment_and_wait()
wait_until_stopped(max_angular_velocity=0.03 rad/s, stable_ms=250)
return_to_heading(candidate_angle)
return_to_start_heading()
```

Use only `nav2_msgs/action/Spin`. Do not add a `/cmd_vel` publisher.

An early strong result maps to:

```text
cancel current Spin goal
wait for Nav2 cancellation acknowledgement
wait for odometry angular velocity to settle
Spin to the saved candidate heading
wait for settle
capture two recheck frames
```

If recheck fails, resume from the interrupted scan position. If VLM confirms
the candidate, stop scanning and leave the camera facing the person. If the
circle finishes with no alert, return to the initial heading.

## Mandatory preflight

- `/spin` Action is ready.
- Required Nav2 lifecycle nodes are active.
- TF and `/odom` are fresh.
- Local costmap is fresh and valid.
- The expanded footprint has no obstacle or person inside the rotation sweep.
- `/cmd_vel` has only the allowlisted Nav2 publisher path.
- The arm is safely stowed.
- No competing teleoperation or direct-drive process is active.
- The fall event has not been cancelled.

Any failed check goes directly to a degraded alert. Direct velocity fallback is
forbidden.

## Cancellation

Cancellation must invalidate delayed inference results. With an active Spin:

```text
cancel Spin
wait for acknowledgement
wait for zero angular velocity
discard motion-period frames
finish the event as cancelled
```

The existing atomic notification claim remains the final cancellation boundary.

## Physical validation gate

The first physical validation requires a cleared area, stowed arm and physical
E-stop or power cut available.

1. One supervised 30-degree segment with notification disabled.
2. Cancel during the segment and verify stop acknowledgement.
3. Supervised 90-degree candidate-return test.
4. Supervised 180-degree scan.
5. Full 360-degree scan with no person near the chassis.
6. Standing, sitting and fallen regression.
7. Only then enable real-contact notification for Spin mode.

The no-motion mode remains the fallback until two supervised Spin cycles pass.
