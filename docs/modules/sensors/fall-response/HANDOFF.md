# Android Fall Response Orin Backend Handoff

Verification date: 2026-08-19

## Implemented boundary

The first delivery is a static, no-motion closed loop. The Android app submits
an authenticated event to the Orin, the Orin persists it in SQLite, captures
five frames from the single front-camera owner, runs local YOLO pose scoring and
SiliconFlow VLM confirmation, then sends one text and at most one image to the
single bound WeChat contact. No component in this flow publishes `/cmd_vel` or
starts Nav2.

Public Android endpoints remain:

```text
GET  /health
POST /api/fall
GET  /api/fall/{event_id}
POST /api/fall/{event_id}/cancel
```

The only public states are `accepted`, `scanning`, `verifying`, `notified`,
`not_fall`, `cancelled`, and `failed`. `event_id` is the idempotency key.

## Components

- `fall_http_gateway`: aiohttp server, Token authentication, validation,
  single-event arbitration and ROS Action dispatch.
- `mobile_fall_coordinator`: `/fall_detection/respond_to_fall` Action server,
  static capture, YOLO, VLM, cancellation window and notification sequencing.
- `project_link_front_camera`: owns `/dev/project_link_front_camera`, publishes
  the low-bandwidth preview and serves `/front_camera/capture_still` from the
  cached high-resolution frame.
- `wechat_notifier_node`: `/fall_detection/send_notification` service backed by
  pinned `wechatbot-sdk==0.3.0`, a single-contact binding and an exactly-once
  notification ledger.

Runtime state defaults to:

```text
~/.local/state/project-link/fall-response/events.sqlite3
~/.config/project_link/wechatbot/credentials.json
~/.local/state/project-link/clawbot/binding.json
~/.local/state/project-link/clawbot/notifications.sqlite3
/home/wte/models/project_link/yolov8n-pose.pt
```

All credentials, binding state and SQLite files must be mode 0600. The YOLO
model is installed explicitly and never downloaded during service startup.

## Deployment gates

1. Build `project_link_emergency_interfaces`, `project_link_console_agent` and
   `project_link_fall_response` with a normal colcon installation.
2. Install `wechatbot-sdk==0.3.0` in the Orin user Python environment.
3. Install and hash `yolov8n-pose.pt` under `/home/wte/models/project_link/`.
4. Copy `deploy/systemd/fall_response.env.example` to the private environment
   file, replace secrets and set mode 0600.
5. Run `ros2 run project_link_fall_response wechatbot_bind` in the foreground;
   the one emergency contact must send the displayed `/bind <code>` command.
6. Install user units. They are not enabled automatically.
7. Validate HTTP, cancellation, static images and real front-camera capture
   before starting `project-link-emergency.target` for phone integration.

Starting the emergency target does not start the base, lidar, mapping or Nav2.

## Remaining gates

- Orin build and automated tests passed through commit `e415791`: 57 tests
  passed, including the authenticated HTTP create/query/idempotency/cancel
  lifecycle; generated ROS interfaces were discoverable and all new systemd
  units verified.
- `yolov8n-pose.pt` is installed with SHA-256
  `c6fa93dd1ee4a2c18c900a45c1d864a1c6f7aba75d84f91648a30b7fb641d212`.
  CPU Torch inference measured about 5.49 FPS on 1280x720 blank frames, below
  the 8 FPS optimization gate. TensorRT 10.3 is present, but a CUDA-enabled
  Torch environment must be isolated and validated before engine export.
- A real `/front_camera/capture_still` call passed at 1280x720 with a 57,996-byte
  JPEG while Nav2 and both fall services stayed inactive. Focus/orientation and
  representative-person five-frame inference still require supervised review.
- A loopback Gateway/Action smoke persisted an event and finished it as
  `cancelled`; no camera, notification or motion service was started.
- `wechatbot-sdk==0.3.0` and its required user-level cryptography runtime are
  installed. The user units are installed but disabled/inactive.
- User-owned WeChat QR login and single-contact binding.
- Android-to-Orin LAN smoke test and 14.9-second cancellation race.
- Confirmed and degraded notification delivery to the real contact.
- Only after two static supervised cycles: add the separately gated Nav2 Spin
  mode. Direct velocity publishing remains forbidden.
