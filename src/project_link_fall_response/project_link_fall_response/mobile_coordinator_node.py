#!/usr/bin/env python3
"""No-motion Android fall-event coordinator."""

from __future__ import annotations

import asyncio
import os
import threading
import time

import rclpy
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from sensor_msgs.msg import CompressedImage
from std_msgs.msg import Bool
from std_srvs.srv import Trigger

from project_link_emergency_interfaces.action import RespondToFall
from project_link_emergency_interfaces.msg import FallResponseStatus
from project_link_emergency_interfaces.srv import CaptureStill, SendFallNotification

from .async_scan import AsyncScanOrchestrator
from .async_vision import AsyncOpenAICompatibleVisionClient
from .core import FallAssessmentError
from .event_store import EventStore, now_ms
from .fall_models import SpecializedFallDetector, YoloWorldPersonDetector
from .fall_response_node import SYSTEM_PROMPT, USER_PROMPT
from .nav2_spin import Nav2SpinAdapter


class MobileFallCoordinator(Node):
    def __init__(self) -> None:
        super().__init__("mobile_fall_coordinator")
        self._declare_parameters()
        self._callbacks = ReentrantCallbackGroup()
        self._scan_mode = str(self.get_parameter("scan_mode").value).strip().lower()
        if self._scan_mode not in {"static", "nav2_spin"}:
            raise ValueError(f"unsupported fall scan mode: {self._scan_mode}")
        self._capture = self.create_client(
            CaptureStill,
            str(self.get_parameter("capture_service").value),
            callback_group=self._callbacks,
        )
        self._notify = self.create_client(
            SendFallNotification,
            str(self.get_parameter("notification_service").value),
            callback_group=self._callbacks,
        )
        self._notification_ready = False
        self.create_subscription(
            Bool,
            "/fall_detection/notification_ready",
            self._on_notification_ready,
            10,
            callback_group=self._callbacks,
        )
        self._status_pub = self.create_publisher(
            FallResponseStatus, "/fall_detection/status", 10
        )
        self._evidence_pub = self.create_publisher(
            CompressedImage, "/fall_detection/evidence/compressed", 5
        )
        self._store = EventStore(str(self.get_parameter("event_db_path").value))
        device = os.environ.get("FALL_YOLO_DEVICE", str(self.get_parameter("yolo_device").value))
        self._fall_detector = SpecializedFallDetector(
            os.environ.get(
                "FALL_SPECIALIZED_MODEL", str(self.get_parameter("specialized_model_path").value)
            ),
            threshold=float(self.get_parameter("specialized_inference_threshold").value),
            device=device,
        )
        self._person_detector = YoloWorldPersonDetector(
            os.environ.get("FALL_WORLD_MODEL", str(self.get_parameter("world_model_path").value)),
            threshold=float(self.get_parameter("world_person_threshold").value),
            device=os.environ.get("FALL_YOLO_DEVICE", str(self.get_parameter("yolo_device").value)),
        )
        steps = max(1, int(self.get_parameter("simulated_scan_steps").value))
        angle_step = 360.0 / steps
        self._scan = AsyncScanOrchestrator(
            angles=tuple(index * angle_step for index in range(steps)),
            frames_per_angle=int(self.get_parameter("frames_per_angle").value),
            recheck_frames=int(self.get_parameter("recheck_frames").value),
            strong_threshold=float(self.get_parameter("strong_fallen_threshold").value),
            weak_threshold=float(self.get_parameter("weak_fallen_threshold").value),
            recheck_frame_threshold=float(
                self.get_parameter("recheck_frame_threshold").value
            ),
            recheck_average_threshold=float(
                self.get_parameter("recheck_average_threshold").value
            ),
            simulated_angle_delay_sec=float(
                self.get_parameter("simulated_angle_delay_sec").value
            ),
        )
        self._vision = AsyncOpenAICompatibleVisionClient(
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            base_url=os.environ.get(
                "OPENAI_BASE_URL", str(self.get_parameter("openai_base_url").value)
            ),
            model=os.environ.get("OPENAI_MODEL", str(self.get_parameter("openai_model").value)),
            timeout_sec=float(
                os.environ.get(
                    "OPENAI_TIMEOUT_SEC", str(self.get_parameter("openai_timeout_sec").value)
                )
            ),
            system_prompt=SYSTEM_PROMPT,
            user_prompt=USER_PROMPT,
        )
        self._async_loop = asyncio.new_event_loop()
        self._async_thread = threading.Thread(target=self._run_async_loop, daemon=True)
        self._async_thread.start()
        self._active_lock = threading.Lock()
        self._reserved = False
        self._cancel = threading.Event()
        self._active_event_id = ""
        self._active_stage = "idle"
        self._active_step = 0
        self._active_total = steps
        self._active_local_confidence = 0.0
        self._active_vlm_confidence = 0.0
        self._active_message = "waiting for a fall event"
        self._nav2 = Nav2SpinAdapter(self, self._callbacks)
        try:
            self._fall_detector.warmup()
            self.get_logger().info("Specialized fall model warmed up")
        except Exception as exc:
            self.get_logger().error(
                f"Specialized fall model warmup failed; World fallback remains available: {exc}"
            )
        self._server = ActionServer(
            self,
            RespondToFall,
            str(self.get_parameter("respond_action").value),
            execute_callback=self._execute,
            goal_callback=self._goal,
            cancel_callback=self._cancel_goal,
            callback_group=self._callbacks,
        )
        self.create_service(
            Trigger,
            "/fall_detection/run_preflight",
            self._run_preflight,
            callback_group=self._callbacks,
        )
        self.create_timer(1.0, self._publish_status, callback_group=self._callbacks)
        self.get_logger().warn(
            "Mobile fall coordinator scan mode is " + self._scan_mode
            + "; startup never initiates motion"
        )

    def _declare_parameters(self) -> None:
        self.declare_parameter("respond_action", "/fall_detection/respond_to_fall")
        self.declare_parameter("capture_service", "/front_camera/capture_still")
        self.declare_parameter("notification_service", "/fall_detection/send_notification")
        self.declare_parameter("scan_mode", "static")
        self.declare_parameter("notification_enabled", True)
        self.declare_parameter(
            "event_db_path", os.path.expanduser("~/.local/state/project-link/fall-response/events.sqlite3")
        )
        self.declare_parameter(
            "specialized_model_path", "/home/wte/models/project_link/human-fall-detection-yolo11.pt"
        )
        self.declare_parameter("world_model_path", "/home/wte/models/yolov8s-worldv2.pt")
        self.declare_parameter("simulated_scan_steps", 12)
        self.declare_parameter("frames_per_angle", 3)
        self.declare_parameter("recheck_frames", 2)
        self.declare_parameter("frame_interval_sec", 0.08)
        self.declare_parameter("capture_timeout_sec", 2.0)
        self.declare_parameter("simulated_angle_delay_sec", 1.0)
        self.declare_parameter("specialized_inference_threshold", 0.05)
        self.declare_parameter("strong_fallen_threshold", 0.60)
        self.declare_parameter("weak_fallen_threshold", 0.25)
        self.declare_parameter("recheck_frame_threshold", 0.55)
        self.declare_parameter("recheck_average_threshold", 0.50)
        self.declare_parameter("world_person_threshold", 0.50)
        self.declare_parameter("yolo_device", "")
        self.declare_parameter("vlm_threshold", 0.70)
        self.declare_parameter(
            "openai_base_url",
            "https://ws-f79uecupn4b5efpv.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        )
        self.declare_parameter("openai_model", "qwen3.8-27b")
        self.declare_parameter("openai_timeout_sec", 20.0)
        self.declare_parameter("notification_timeout_sec", 90.0)
        self.declare_parameter("spin_action", "/spin")
        self.declare_parameter("odom_topic", "/odom")
        self.declare_parameter("local_costmap_topic", "/local_costmap/costmap")
        self.declare_parameter("cmd_vel_topic", "/cmd_vel")
        self.declare_parameter("odom_frame", "odom")
        self.declare_parameter("base_frame", "base_footprint")
        self.declare_parameter("arm_status_topic", "/visual_grasp/status")
        self.declare_parameter(
            "nav2_lifecycle_services",
            [
                "/behavior_server/get_state",
                "/controller_server/get_state",
                "/bt_navigator/get_state",
            ],
        )
        self.declare_parameter(
            "competing_action_cancel_services",
            [
                "/navigate_to_pose/_action/cancel_goal",
                "/navigate_through_poses/_action/cancel_goal",
            ],
        )
        self.declare_parameter("cancel_competing_actions", True)
        self.declare_parameter(
            "allowed_cmd_vel_publishers", ["velocity_smoother", "behavior_server"]
        )
        self.declare_parameter("require_arm_torque_off", True)
        self.declare_parameter("tf_ttl_sec", 0.50)
        self.declare_parameter("odom_ttl_sec", 0.50)
        self.declare_parameter("costmap_ttl_sec", 1.50)
        self.declare_parameter("rotation_clearance_radius_m", 0.42)
        self.declare_parameter("rotation_obstacle_cost_threshold", 80)
        self.declare_parameter("stop_angular_velocity_rps", 0.03)
        self.declare_parameter("stop_stable_sec", 0.25)
        self.declare_parameter("stop_timeout_sec", 3.0)
        self.declare_parameter("spin_cancel_timeout_sec", 2.0)
        self.declare_parameter("spin_timeout_sec", 12.0)

    def _run_async_loop(self) -> None:
        asyncio.set_event_loop(self._async_loop)
        self._async_loop.run_forever()

    def _on_notification_ready(self, message: Bool) -> None:
        self._notification_ready = bool(message.data)

    def _publish_status(self) -> None:
        event = self._store.active_event()
        preflight = self._nav2.last_preflight
        message = FallResponseStatus()
        message.stamp = self.get_clock().now().to_msg()
        message.scan_mode = self._scan_mode
        message.service_ready = True
        message.event_active = event is not None
        message.active_event_id = "" if event is None else str(event["event_id"])
        message.stage = (
            self._active_stage if event is not None else "idle"
        )
        message.scan_step = int(self._active_step if event is not None else 0)
        message.scan_total = int(self._active_total)
        message.current_heading_deg = (
            float(self._nav2.current_heading_deg)
            if self._scan_mode == "nav2_spin"
            else 0.0
        )
        message.target_heading_deg = (
            float(self._nav2.target_heading_deg)
            if self._scan_mode == "nav2_spin"
            else 0.0
        )
        message.local_confidence = float(
            self._active_local_confidence if event is not None else 0.0
        )
        message.vlm_confidence = float(
            self._active_vlm_confidence if event is not None else 0.0
        )
        message.motion_active = bool(self._nav2.motion_active)
        message.camera_ready = bool(self._capture.service_is_ready())
        message.specialized_model_ready = bool(self._fall_detector.ready)
        message.world_model_ready = bool(self._person_detector.ready)
        message.vlm_ready = bool(os.environ.get("OPENAI_API_KEY", ""))
        message.notification_ready = bool(
            self._notify.service_is_ready() and self._notification_ready
        )
        message.nav2_action_ready = bool(self._nav2.action_ready())
        message.nav2_lifecycle_ready = bool(preflight.lifecycle_ready)
        message.tf_ready = bool(preflight.tf_ready)
        message.odom_ready = bool(preflight.odom_ready)
        message.costmap_ready = bool(preflight.costmap_ready)
        message.rotation_clear = bool(preflight.rotation_clear)
        message.cmd_vel_clear = bool(preflight.cmd_vel_clear)
        message.arm_safe = bool(preflight.arm_safe)
        message.message = self._active_message if event is not None else (
            "static mode ready; no motion preflight is required"
            if self._scan_mode == "static"
            else preflight.message
        )
        self._status_pub.publish(message)

    def _set_active_progress(
        self,
        stage: str,
        message: str,
        *,
        step: int | None = None,
        total: int | None = None,
        local: float | None = None,
        vlm: float | None = None,
    ) -> None:
        self._active_stage = stage
        self._active_message = message
        if step is not None:
            self._active_step = int(step)
        if total is not None:
            self._active_total = int(total)
        if local is not None:
            self._active_local_confidence = float(local)
        if vlm is not None:
            self._active_vlm_confidence = float(vlm)

    def _publish_evidence(self, jpeg_data: bytes) -> None:
        if not jpeg_data:
            return
        message = CompressedImage()
        message.header.stamp = self.get_clock().now().to_msg()
        message.header.frame_id = "front_camera"
        message.format = "jpeg"
        message.data = jpeg_data
        self._evidence_pub.publish(message)

    def _run_preflight(self, _request, response):
        if self._scan_mode != "nav2_spin":
            response.success = True
            response.message = "static mode selected; no robot motion will be used"
            return response
        snapshot = self._nav2.preflight(lambda: False, cancel_competing=False)
        response.success = bool(snapshot.ready)
        response.message = snapshot.message
        return response

    def _goal(self, request: RespondToFall.Goal) -> GoalResponse:
        event = self._store.get(request.event_id)
        if event is None or event["status"] != "accepted":
            return GoalResponse.REJECT
        with self._active_lock:
            if self._reserved:
                return GoalResponse.REJECT
            self._reserved = True
        self._cancel.clear()
        self._active_event_id = request.event_id
        self._set_active_progress(
            "accepted",
            "fall event accepted",
            step=0,
            local=0.0,
            vlm=0.0,
        )
        return GoalResponse.ACCEPT

    def _cancel_goal(self, _goal_handle) -> CancelResponse:
        self._cancel.set()
        return CancelResponse.ACCEPT

    def _cancelled(self, goal_handle) -> bool:
        event = self._store.get(goal_handle.request.event_id)
        return (
            self._cancel.is_set()
            or goal_handle.is_cancel_requested
            or event is None
            or event["status"] == "cancelled"
        )

    def _feedback(
        self,
        goal_handle,
        stage: str,
        message: str,
        *,
        local: float = 0.0,
        vlm: float = 0.0,
        degraded: bool = False,
        step: int = 0,
        total: int = 0,
    ) -> None:
        self._set_active_progress(
            stage,
            message,
            step=step,
            total=total if total else None,
            local=local,
            vlm=vlm,
        )
        feedback = RespondToFall.Feedback()
        feedback.stage = stage
        feedback.message = message
        feedback.local_confidence = float(local)
        feedback.vlm_confidence = float(vlm)
        feedback.degraded = bool(degraded)
        feedback.scan_step = int(step)
        feedback.scan_total = int(total)
        goal_handle.publish_feedback(feedback)

    def _wait_ros_future(self, future, goal_handle, timeout_sec: float) -> bool:
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            if future.done():
                return True
            if self._cancelled(goal_handle):
                return False
            time.sleep(0.02)
        return False

    def _scan_feedback(
        self,
        goal_handle,
        event_id: str,
        stage: str,
        message: str,
        step: int,
        total: int,
        confidence: float,
    ) -> None:
        self._set_active_progress(
            stage,
            message,
            step=step,
            total=total,
            local=confidence,
        )
        self._store.update(
            event_id,
            status="scanning",
            stage=stage,
            message=message,
            local_confidence=confidence,
        )
        self._feedback(
            goal_handle,
            stage,
            message,
            local=confidence,
            step=step,
            total=total,
        )

    def _capture_frames(
        self,
        goal_handle,
        angle_deg: float,
        count: int,
        stage: str,
        step: int,
        total: int,
    ) -> list[bytes]:
        timeout = float(self.get_parameter("capture_timeout_sec").value)
        if not self._capture.wait_for_service(timeout_sec=timeout):
            raise RuntimeError("front camera capture service is unavailable")
        count = max(1, int(count))
        interval = max(0.0, float(self.get_parameter("frame_interval_sec").value))
        frames: list[bytes] = []
        for index in range(count):
            if self._cancelled(goal_handle):
                break
            future = self._capture.call_async(CaptureStill.Request())
            if not self._wait_ros_future(future, goal_handle, timeout):
                raise RuntimeError("front camera capture timed out or was cancelled")
            response = future.result()
            if response is None or not response.success or not response.jpeg_data:
                raise RuntimeError(response.message if response else "front camera returned no response")
            frames.append(bytes(response.jpeg_data))
            self._feedback(
                goal_handle,
                stage,
                f"captured frame {index + 1}/{count} at angle {angle_deg:.0f} degrees",
                step=step,
                total=total,
            )
            if index + 1 < count:
                time.sleep(interval)
        return frames

    def _assess_vlm(self, images: list[tuple[str, bytes]], goal_handle):
        future = asyncio.run_coroutine_threadsafe(self._vision.assess_many(images), self._async_loop)
        while not future.done():
            if self._cancelled(goal_handle):
                future.cancel()
                raise RuntimeError("cancelled during VLM assessment")
            time.sleep(0.05)
        return future.result()

    def _wait_until_notification(self, event_id: str, goal_handle) -> bool:
        while True:
            event = self._store.get(event_id)
            if event is None or self._cancelled(goal_handle):
                return False
            remaining = int(event["notify_not_before_ms"]) - now_ms()
            if remaining <= 0:
                return True
            self._feedback(goal_handle, "waiting_cancel_window", f"notification allowed in {remaining} ms")
            time.sleep(min(0.1, remaining / 1000.0))

    def _notify_contact(
        self,
        goal_handle,
        event,
        *,
        degraded: bool,
        confidence: float,
        reason: str,
        jpeg_data: bytes,
    ):
        if not self._notify.wait_for_service(timeout_sec=2.0):
            raise RuntimeError("WeChat notification service is unavailable")
        request = SendFallNotification.Request()
        request.event_id = event["event_id"]
        request.degraded = degraded
        request.confidence = float(confidence)
        request.reason = reason
        request.occurred_at_ms = int(event["occurred_at_ms"])
        request.jpeg_data = list(jpeg_data)
        future = self._notify.call_async(request)
        if not self._wait_ros_future(
            future, goal_handle, float(self.get_parameter("notification_timeout_sec").value)
        ):
            raise RuntimeError("WeChat notification service timed out")
        response = future.result()
        if response is None:
            raise RuntimeError("WeChat notification service returned no response")
        return response

    def _result(self, event, *, local: float, vlm: float, reason: str):
        result = RespondToFall.Result()
        result.final_status = str(event["status"])
        result.degraded = bool(event["degraded"])
        result.local_confidence = float(local)
        result.vlm_confidence = float(vlm)
        result.reason = reason
        result.notification_attempted = event["notification_attempted_at_ms"] is not None
        result.notification_success = event["notification_succeeded_at_ms"] is not None
        result.message = str(event["message"])
        return result

    def _execute(self, goal_handle):
        event_id = goal_handle.request.event_id
        local_confidence = 0.0
        vlm_confidence = 0.0
        reason = ""
        notification_image = b""
        degraded = False
        candidate_heading = None
        try:
            preflight_message = (
                "validating Nav2 Spin, localization, costmap and motion ownership"
                if self._scan_mode == "nav2_spin"
                else "starting static asynchronous scan fallback"
            )
            self._store.update(
                event_id,
                status="scanning",
                stage="preflight",
                message=preflight_message,
            )
            self._feedback(
                goal_handle,
                "preflight",
                preflight_message,
            )
            try:
                move_to_heading = None
                if self._scan_mode == "nav2_spin":
                    preflight = self._nav2.preflight(
                        lambda: self._cancelled(goal_handle),
                        cancel_competing=True,
                    )
                    if not preflight.ready:
                        raise RuntimeError(
                            "Nav2 Spin preflight failed: " + preflight.message
                        )
                    move_to_heading = self._nav2.go_to_heading
                outcome = self._scan.run(
                    capture=lambda angle, count, stage, step, total: self._capture_frames(
                        goal_handle, angle, count, stage, step, total
                    ),
                    infer_fall=self._fall_detector.assess,
                    infer_people=self._person_detector.assess,
                    cancelled=lambda: self._cancelled(goal_handle),
                    feedback=lambda stage, message, step, total, confidence: self._scan_feedback(
                        goal_handle, event_id, stage, message, step, total, confidence
                    ),
                    move_to_heading=move_to_heading,
                )
                if outcome.kind == "cancelled":
                    self._nav2.cancel_current_segment_and_wait()
                    event = self._store.get(event_id)
                    goal_handle.canceled()
                    return self._result(event, local=0.0, vlm=0.0, reason="cancelled")
                local_confidence = outcome.confidence
                reason = outcome.reason
                notification_image = outcome.notification_image
                candidate_heading = outcome.angle_deg
                self._publish_evidence(notification_image)
                if outcome.kind == "degraded":
                    degraded = True
                    vlm_images: list[tuple[str, bytes]] = []
                else:
                    vlm_images = list(outcome.vlm_images)
            except Exception as exc:
                self._nav2.cancel_current_segment_and_wait()
                if self._cancelled(goal_handle):
                    event = self._store.get(event_id)
                    goal_handle.canceled()
                    return self._result(event, local=local_confidence, vlm=0.0, reason="cancelled")
                degraded = True
                reason = f"local asynchronous visual assessment unavailable: {exc}"
                vlm_images = []

            self._store.update(
                event_id,
                status="verifying",
                stage="vlm_request" if vlm_images else "degraded_wait",
                message=reason,
                local_confidence=local_confidence,
                degraded=int(degraded),
                degraded_reason=reason if degraded else "",
            )
            if vlm_images:
                self._feedback(
                    goal_handle,
                    "vlm_request",
                    f"requesting OpenAI-compatible review of {len(vlm_images)} labeled images",
                    local=local_confidence,
                )
                try:
                    assessment = self._assess_vlm(vlm_images, goal_handle)
                    vlm_confidence = assessment.confidence
                    reason = assessment.reason
                    self._active_vlm_confidence = float(vlm_confidence)
                    if not assessment.fall_suspected or assessment.confidence < float(
                        self.get_parameter("vlm_threshold").value
                    ):
                        if self._scan_mode == "nav2_spin" and candidate_heading is not None:
                            self._feedback(
                                goal_handle,
                                "return_to_start",
                                "VLM did not confirm a fall; restoring the initial heading",
                                local=local_confidence,
                                vlm=vlm_confidence,
                            )
                            if not self._nav2.return_to_start(
                                lambda: self._cancelled(goal_handle)
                            ):
                                raise RuntimeError(
                                    "initial heading could not be restored after VLM rejection"
                                )
                        event = self._store.update(
                            event_id,
                            status="not_fall",
                            stage="completed",
                            message=reason,
                            local_confidence=local_confidence,
                            vlm_confidence=vlm_confidence,
                            assessment_reason=reason,
                        )
                        goal_handle.succeed()
                        return self._result(
                            event, local=local_confidence, vlm=vlm_confidence, reason=reason
                        )
                except (FallAssessmentError, Exception) as exc:
                    if self._cancelled(goal_handle):
                        event = self._store.get(event_id)
                        goal_handle.canceled()
                        return self._result(event, local=local_confidence, vlm=vlm_confidence, reason="cancelled")
                    degraded = True
                    reason = f"VLM could not confirm the fall: {exc}"
                    self._store.update(
                        event_id,
                        status="verifying",
                        stage="degraded_wait",
                        message=reason,
                        degraded=1,
                        degraded_reason=reason,
                    )

            if not bool(self.get_parameter("notification_enabled").value):
                event = self._store.update(
                    event_id,
                    status="failed",
                    stage="notification_suppressed",
                    message=(
                        "assessment completed, but real-contact notification is disabled"
                    ),
                    local_confidence=local_confidence,
                    vlm_confidence=vlm_confidence,
                    assessment_reason=reason,
                    degraded=int(degraded),
                    degraded_reason=reason if degraded else "",
                )
                goal_handle.abort()
                return self._result(
                    event,
                    local=local_confidence,
                    vlm=vlm_confidence,
                    reason=reason,
                )

            if not self._wait_until_notification(event_id, goal_handle):
                event = self._store.get(event_id)
                goal_handle.canceled()
                return self._result(event, local=local_confidence, vlm=vlm_confidence, reason="cancelled")
            event, claimed = self._store.claim_notification(event_id)
            if not claimed:
                if event and event["status"] == "cancelled":
                    goal_handle.canceled()
                else:
                    goal_handle.abort()
                return self._result(event, local=local_confidence, vlm=vlm_confidence, reason=reason)
            self._feedback(
                goal_handle,
                "notifying",
                "sending the single-contact WeChat alert",
                local=local_confidence,
                vlm=vlm_confidence,
                degraded=degraded,
            )
            attempted_at = now_ms()
            response = self._notify_contact(
                goal_handle,
                event,
                degraded=degraded,
                confidence=vlm_confidence if not degraded else max(local_confidence, vlm_confidence),
                reason=reason,
                jpeg_data=notification_image,
            )
            status = "notified" if response.text_success else "failed"
            event = self._store.update(
                event_id,
                status=status,
                stage="completed",
                message=response.message,
                local_confidence=local_confidence,
                vlm_confidence=vlm_confidence,
                assessment_reason=reason,
                degraded=int(degraded),
                degraded_reason=reason if degraded else "",
                notification_attempted_at_ms=attempted_at,
                notification_succeeded_at_ms=now_ms() if response.text_success else None,
                text_success=int(response.text_success),
                image_success=int(response.image_success),
            )
            goal_handle.succeed() if response.text_success else goal_handle.abort()
            self._feedback(goal_handle, "completed", response.message, degraded=degraded)
            return self._result(event, local=local_confidence, vlm=vlm_confidence, reason=reason)
        except Exception as exc:
            event = self._store.update(
                event_id,
                status="failed",
                stage="completed",
                message=f"fall response failed: {exc}",
                local_confidence=local_confidence,
                vlm_confidence=vlm_confidence,
                assessment_reason=reason,
                degraded=int(degraded),
            )
            goal_handle.abort()
            return self._result(event, local=local_confidence, vlm=vlm_confidence, reason=reason)
        finally:
            if self._nav2.motion_active:
                self._nav2.cancel_current_segment_and_wait()
            with self._active_lock:
                self._reserved = False
            self._cancel.clear()
            self._active_event_id = ""
            self._set_active_progress(
                "idle",
                "waiting for a fall event",
                step=0,
                local=0.0,
                vlm=0.0,
            )

    def destroy_node(self):
        self._cancel.set()
        self._nav2.shutdown()
        self._async_loop.call_soon_threadsafe(self._async_loop.stop)
        self._async_thread.join(timeout=2.0)
        return super().destroy_node()


def main() -> None:
    rclpy.init()
    node = MobileFallCoordinator()
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        executor.shutdown()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
