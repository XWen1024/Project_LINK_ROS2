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

from project_link_emergency_interfaces.action import RespondToFall
from project_link_emergency_interfaces.srv import CaptureStill, SendFallNotification

from .async_vision import AsyncOpenAICompatibleVisionClient
from .core import FallAssessmentError
from .event_store import EventStore, now_ms
from .fall_response_node import SYSTEM_PROMPT, USER_PROMPT
from .pose import YoloPoseDetector


class MobileFallCoordinator(Node):
    def __init__(self) -> None:
        super().__init__("mobile_fall_coordinator")
        self._declare_parameters()
        self._callbacks = ReentrantCallbackGroup()
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
        self._store = EventStore(str(self.get_parameter("event_db_path").value))
        self._detector = YoloPoseDetector(
            os.environ.get("FALL_YOLO_MODEL", str(self.get_parameter("model_path").value)),
            detection_threshold=float(self.get_parameter("detection_threshold").value),
            keypoint_threshold=float(self.get_parameter("keypoint_threshold").value),
            candidate_threshold=float(self.get_parameter("candidate_threshold").value),
            stable_frames=int(self.get_parameter("stable_frames").value),
            device=os.environ.get("FALL_YOLO_DEVICE", str(self.get_parameter("yolo_device").value)),
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
        self._server = ActionServer(
            self,
            RespondToFall,
            str(self.get_parameter("respond_action").value),
            execute_callback=self._execute,
            goal_callback=self._goal,
            cancel_callback=self._cancel_goal,
            callback_group=self._callbacks,
        )
        self.get_logger().warn("Mobile fall coordinator is in STATIC NO-MOTION mode")

    def _declare_parameters(self) -> None:
        self.declare_parameter("respond_action", "/fall_detection/respond_to_fall")
        self.declare_parameter("capture_service", "/front_camera/capture_still")
        self.declare_parameter("notification_service", "/fall_detection/send_notification")
        self.declare_parameter(
            "event_db_path", os.path.expanduser("~/.local/state/project-link/fall-response/events.sqlite3")
        )
        self.declare_parameter("model_path", "/home/wte/models/project_link/yolov8n-pose.pt")
        self.declare_parameter("frame_count", 5)
        self.declare_parameter("frame_interval_sec", 0.20)
        self.declare_parameter("capture_timeout_sec", 2.0)
        self.declare_parameter("detection_threshold", 0.45)
        self.declare_parameter("keypoint_threshold", 0.30)
        self.declare_parameter("candidate_threshold", 0.65)
        self.declare_parameter("stable_frames", 3)
        self.declare_parameter("yolo_device", "")
        self.declare_parameter("vlm_threshold", 0.70)
        self.declare_parameter(
            "openai_base_url",
            "https://ws-f79uecupn4b5efpv.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        )
        self.declare_parameter("openai_model", "qwen3.8-27b")
        self.declare_parameter("openai_timeout_sec", 20.0)
        self.declare_parameter("notification_timeout_sec", 90.0)

    def _run_async_loop(self) -> None:
        asyncio.set_event_loop(self._async_loop)
        self._async_loop.run_forever()

    def _goal(self, request: RespondToFall.Goal) -> GoalResponse:
        event = self._store.get(request.event_id)
        if event is None or event["status"] != "accepted":
            return GoalResponse.REJECT
        with self._active_lock:
            if self._reserved:
                return GoalResponse.REJECT
            self._reserved = True
        self._cancel.clear()
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

    def _capture_frames(self, goal_handle) -> list[bytes]:
        timeout = float(self.get_parameter("capture_timeout_sec").value)
        if not self._capture.wait_for_service(timeout_sec=timeout):
            raise RuntimeError("front camera capture service is unavailable")
        count = max(1, int(self.get_parameter("frame_count").value))
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
            self._feedback(goal_handle, "capturing", "capturing static pose frames", step=index + 1, total=count)
            if index + 1 < count:
                time.sleep(interval)
        return frames

    def _assess_vlm(self, jpeg_data: bytes, goal_handle):
        future = asyncio.run_coroutine_threadsafe(self._vision.assess(jpeg_data), self._async_loop)
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
        frames: list[bytes] = []
        degraded = False
        try:
            self._store.update(event_id, status="scanning", stage="preflight", message="static visual preflight")
            self._feedback(goal_handle, "preflight", "static no-motion visual assessment")
            try:
                frames = self._capture_frames(goal_handle)
                self._feedback(goal_handle, "local_inference", "running local YOLO pose")
                pose = self._detector.assess(frames)
                local_confidence = pose.confidence
                reason = pose.reason
                if pose.outcome == "not_fall":
                    event = self._store.update(
                        event_id,
                        status="not_fall",
                        stage="completed",
                        message=reason,
                        local_confidence=local_confidence,
                        assessment_reason=reason,
                    )
                    goal_handle.succeed()
                    return self._result(event, local=local_confidence, vlm=0.0, reason=reason)
                if pose.outcome == "candidate":
                    frames = [frames[min(pose.best_frame_index, len(frames) - 1)]]
                else:
                    degraded = True
            except Exception as exc:
                if self._cancelled(goal_handle):
                    event = self._store.get(event_id)
                    goal_handle.canceled()
                    return self._result(event, local=local_confidence, vlm=0.0, reason="cancelled")
                degraded = True
                reason = f"local visual assessment unavailable: {exc}"

            self._store.update(
                event_id,
                status="verifying",
                stage="vlm_request" if not degraded else "degraded_wait",
                message=reason,
                local_confidence=local_confidence,
                degraded=int(degraded),
                degraded_reason=reason if degraded else "",
            )
            jpeg_data = frames[0] if frames else b""
            if not degraded:
                self._feedback(
                    goal_handle,
                    "vlm_request",
                    "requesting OpenAI-compatible visual confirmation",
                    local=local_confidence,
                )
                try:
                    assessment = self._assess_vlm(jpeg_data, goal_handle)
                    vlm_confidence = assessment.confidence
                    reason = assessment.reason
                    if not assessment.fall_suspected or assessment.confidence < float(
                        self.get_parameter("vlm_threshold").value
                    ):
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
                jpeg_data=jpeg_data,
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
            with self._active_lock:
                self._reserved = False
            self._cancel.clear()

    def destroy_node(self):
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
