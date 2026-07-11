#!/usr/bin/env python3
"""Fall assessment action server and Feishu alert coordinator."""

from __future__ import annotations

import threading
import time
import uuid

import rclpy
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from std_msgs.msg import String

from project_link_emergency_interfaces.action import AssessFall
from project_link_emergency_interfaces.srv import CaptureStill, ConfirmFallAlert

from .core import FallAssessmentError, FeishuBotClient, SiliconFlowVisionClient


SYSTEM_PROMPT = (
    "You are an emergency fall-detection assistant for an eldercare robot. "
    "Return only strict JSON with keys fall_suspected, confidence, and reason. "
    "Use fall_suspected=true only when the visible person appears to have fallen, "
    "is lying on the floor, or is in a posture strongly consistent with a fall."
)
USER_PROMPT = (
    "Assess this camera image for a possible fallen person. "
    "Return only JSON such as {\"fall_suspected\": false, \"confidence\": 0.0, \"reason\": \"...\"}."
)


class FallResponseNode(Node):
    def __init__(self) -> None:
        super().__init__("fall_response_node")
        self._declare_parameters()
        self._callback_group = ReentrantCallbackGroup()
        self._capture_client = self.create_client(
            CaptureStill,
            str(self.get_parameter("capture_service").value),
            callback_group=self._callback_group,
        )
        self._tts_pub = self.create_publisher(String, str(self.get_parameter("tts_topic").value), 10)
        self._active_lock = threading.Lock()
        self._goal_active = False
        self._pending_alert_id = ""
        self._confirm_event = threading.Event()
        self._confirmed = False
        self._cancel_event = threading.Event()
        self._vision_client = self._make_vision_client()
        self._notification_client = FeishuBotClient()
        self._confirm_service = self.create_service(
            ConfirmFallAlert,
            str(self.get_parameter("confirm_service").value),
            self._confirm_alert,
            callback_group=self._callback_group,
        )
        self._action_server = ActionServer(
            self,
            AssessFall,
            str(self.get_parameter("assess_action").value),
            execute_callback=self._execute_assess_fall,
            goal_callback=self._accept_goal,
            cancel_callback=self._cancel_goal,
            callback_group=self._callback_group,
        )
        self.get_logger().info("Fall response action server ready")

    def _declare_parameters(self) -> None:
        self.declare_parameter("assess_action", "/fall_detection/assess_fall")
        self.declare_parameter("capture_service", "/fall_detection/capture_still")
        self.declare_parameter("confirm_service", "/fall_detection/confirm_alert")
        self.declare_parameter("tts_topic", "/voice/tts_text")
        self.declare_parameter(
            "alert_tts_text",
            "您看起来摔倒了，正在为您呼叫紧急联系人。",
        )
        self.declare_parameter("confirmation_timeout_sec", 15.0)
        self.declare_parameter("confidence_threshold", 0.70)
        self.declare_parameter("capture_timeout_sec", 3.0)
        self.declare_parameter("siliconflow_base_url", "https://api.siliconflow.cn/v1")
        self.declare_parameter("siliconflow_model", "Qwen/Qwen2.5-VL-72B-Instruct")
        self.declare_parameter("siliconflow_request_timeout_sec", 20.0)
        self.declare_parameter("siliconflow_system_prompt", SYSTEM_PROMPT)
        self.declare_parameter("siliconflow_user_prompt", USER_PROMPT)

    def _make_vision_client(self) -> SiliconFlowVisionClient:
        return SiliconFlowVisionClient.from_environment(
            base_url=str(self.get_parameter("siliconflow_base_url").value),
            model=str(self.get_parameter("siliconflow_model").value),
            request_timeout_sec=float(self.get_parameter("siliconflow_request_timeout_sec").value),
            system_prompt=str(self.get_parameter("siliconflow_system_prompt").value),
            user_prompt=str(self.get_parameter("siliconflow_user_prompt").value),
        )

    def _accept_goal(self, _goal: AssessFall.Goal) -> GoalResponse:
        with self._active_lock:
            if self._goal_active:
                return GoalResponse.REJECT
            self._goal_active = True
            self._cancel_event.clear()
            self._confirm_event.clear()
            self._pending_alert_id = ""
            self._confirmed = False
        return GoalResponse.ACCEPT

    def _cancel_goal(self, _goal_handle) -> CancelResponse:
        self._cancel_event.set()
        self._confirm_event.set()
        return CancelResponse.ACCEPT

    def _confirm_alert(
        self,
        request: ConfirmFallAlert.Request,
        response: ConfirmFallAlert.Response,
    ) -> ConfirmFallAlert.Response:
        with self._active_lock:
            if not self._pending_alert_id or request.alert_id != self._pending_alert_id:
                response.success = False
                response.message = "unknown or expired alert_id"
                return response
            self._confirmed = bool(request.confirmed)
            self._confirm_event.set()
        response.success = True
        response.message = "alert confirmed" if request.confirmed else "alert cancelled"
        return response

    def _execute_assess_fall(self, goal_handle):
        goal = goal_handle.request
        alert_id = str(uuid.uuid4())
        result = AssessFall.Result()
        result.alert_id = alert_id
        try:
            capture = self._capture_image(goal_handle, alert_id)
            if capture is None:
                result.message = "capture failed or action cancelled"
                return self._finish(goal_handle, result)
            assessment = self._assess_image(goal_handle, alert_id, bytes(capture.jpeg_data))
            result.fall_suspected = assessment.fall_suspected
            result.confidence = float(assessment.confidence)
            result.reason = assessment.reason
            threshold = float(self.get_parameter("confidence_threshold").value)
            if not assessment.fall_suspected or assessment.confidence < threshold:
                result.message = (
                    f"no emergency notification: fall_suspected={assessment.fall_suspected}, "
                    f"confidence={assessment.confidence:.2f}, threshold={threshold:.2f}"
                )
                return self._finish(goal_handle, result)
            if self._cancel_event.is_set() or goal_handle.is_cancel_requested:
                return self._finish_cancelled(goal_handle, result, "cancelled before alert")
            result.alert_started = True
            self._begin_confirmation(alert_id)
            self._publish_feedback(goal_handle, alert_id, "awaiting_confirmation", assessment.confidence, assessment.reason)
            self._say(str(self.get_parameter("alert_tts_text").value))
            decision = self._wait_for_confirmation(goal_handle, assessment.confidence)
            with self._active_lock:
                self._pending_alert_id = ""
            if decision == "cancelled":
                return self._finish_cancelled(goal_handle, result, "cancelled before emergency notification")
            notification = self._notification_client.send_fall_alert(
                alert_id=alert_id,
                confidence=assessment.confidence,
                reason=assessment.reason,
            )
            result.notification_attempted = bool(notification.attempted)
            result.notification_success = bool(notification.success)
            result.message = notification.message
            self._publish_feedback(goal_handle, alert_id, "notification_finished", assessment.confidence, notification.message)
            return self._finish(goal_handle, result)
        except FallAssessmentError as exc:
            result.message = str(exc)
            return self._finish(goal_handle, result)
        except Exception as exc:
            self.get_logger().error(f"Fall response failed: {exc}")
            result.message = f"fall response failed: {exc}"
            return self._finish(goal_handle, result)
        finally:
            with self._active_lock:
                self._goal_active = False
                self._pending_alert_id = ""
                self._confirm_event.clear()
                self._cancel_event.clear()

    def _capture_image(self, goal_handle, alert_id: str):
        self._publish_feedback(goal_handle, alert_id, "capture", 0.0, "capturing second-camera still")
        if not self._capture_client.wait_for_service(timeout_sec=float(self.get_parameter("capture_timeout_sec").value)):
            self.get_logger().error("CaptureStill service is unavailable")
            return None
        future = self._capture_client.call_async(CaptureStill.Request())
        if not self._wait_future(future, float(self.get_parameter("capture_timeout_sec").value)):
            return None
        response = future.result()
        if response is None or not response.success or not response.jpeg_data:
            message = response.message if response else "no response"
            self.get_logger().error(f"CaptureStill failed: {message}")
            return None
        return response

    def _assess_image(self, goal_handle, alert_id: str, jpeg_data: bytes):
        self._publish_feedback(goal_handle, alert_id, "vision_request", 0.0, "calling SiliconFlow vision model")
        assessment = self._vision_client.assess(jpeg_data)
        self._publish_feedback(goal_handle, alert_id, "vision_result", assessment.confidence, assessment.reason)
        return assessment

    def _begin_confirmation(self, alert_id: str) -> None:
        with self._active_lock:
            self._pending_alert_id = alert_id
            self._confirmed = False
            self._confirm_event.clear()

    def _wait_for_confirmation(self, goal_handle, confidence: float) -> str:
        timeout = max(0.0, float(self.get_parameter("confirmation_timeout_sec").value))
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            if self._cancel_event.is_set() or goal_handle.is_cancel_requested:
                return "cancelled"
            if self._confirm_event.wait(timeout=0.1):
                with self._active_lock:
                    return "confirmed" if self._confirmed else "cancelled"
        with self._active_lock:
            alert_id = self._pending_alert_id
        self._publish_feedback(
            goal_handle,
            alert_id,
            "confirmation_timeout",
            confidence,
            "sending Feishu emergency alert after timeout",
        )
        return "timeout"

    def _wait_future(self, future, timeout_sec: float) -> bool:
        end = time.monotonic() + max(timeout_sec, 0.1)
        while time.monotonic() < end:
            if future.done():
                return True
            if self._cancel_event.is_set():
                return False
            time.sleep(0.02)
        return False

    def _publish_feedback(self, goal_handle, alert_id: str, stage: str, confidence: float, message: str) -> None:
        feedback = AssessFall.Feedback()
        feedback.alert_id = alert_id
        feedback.stage = stage
        feedback.confidence = float(confidence)
        feedback.message = message
        goal_handle.publish_feedback(feedback)

    def _say(self, text: str) -> None:
        message = String()
        message.data = text
        self._tts_pub.publish(message)

    def _finish(self, goal_handle, result: AssessFall.Result) -> AssessFall.Result:
        if goal_handle.is_cancel_requested or self._cancel_event.is_set():
            goal_handle.canceled()
        else:
            goal_handle.succeed()
        return result

    def _finish_cancelled(self, goal_handle, result: AssessFall.Result, message: str) -> AssessFall.Result:
        result.message = message
        goal_handle.canceled()
        return result


def main() -> None:
    rclpy.init()
    node = FallResponseNode()
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        executor.shutdown()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
