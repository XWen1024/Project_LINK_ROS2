"""Headless ROS 2 node for local YOLO-World tracking and SO-101 grasping."""
from __future__ import annotations

import csv
from collections import deque
import json
import shutil
import socket
from pathlib import Path
from statistics import median
import subprocess
import threading
import time
from typing import Any

import rclpy
from rcl_interfaces.msg import SetParametersResult
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.parameter import Parameter
from rclpy.qos import QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import CompressedImage, Range
from std_msgs.msg import String
from std_srvs.srv import SetBool, Trigger
from wheeltec_robot_msg.action import TrackAndGrasp
from wheeltec_robot_msg.msg import VisualGraspStatus
from wheeltec_robot_msg.srv import SetGripper, SetTarget

from .camera import native_mjpeg_command, pop_native_jpeg
from .core import (
    ALL_JOINTS,
    Detection,
    RuntimeStore,
    SO101Arm,
    ServoState,
    TofReading,
    VisualServoController,
    YoloWorldTracker,
)
from .external_tracker import ExternalYoloWorldTracker


PARAMETER_DEFAULTS: dict[str, Any] = {
    "robot_namespace": "/visual_grasp",
    "camera_device": "/dev/project_link_arm_camera",
    "camera_width": 1280,
    "camera_height": 720,
    "camera_fps": 30.0,
    "preview_fps": 30.0,
    "prefer_native_mjpeg": True,
    "camera_reopen_interval_sec": 2.0,
    "jpeg_quality": 75,
    "detector_backend": "external_cuda",
    "detector_stale_timeout_sec": 1.0,
    "model_path": "/home/wte/models/yolov8s-worldv2.pt",
    "yolo_device": "cpu",
    "yolo_conf_threshold": 0.15,
    "yolo_max_lost_frames": 15,
    "yolo_infer_interval_sec": 0.0,
    "yolo_ema_alpha": 0.6,
    "yolo_max_center_jump_ratio": 0.12,
    "yolo_max_area_change_ratio": 1.8,
    "yolo_outlier_hold_frames": 4,
    "yolo_track_iou_weight": 0.5,
    "robot_port": "/dev/project_link_so101",
    "robot_id": "so101_slave",
    "auto_connect_arm": False,
    "pan_gain": 25.0,
    "tilt_gain": 15.0,
    "pan_direction": 1.0,
    "tilt_direction": -1.0,
    "centering_tilt_motion_enabled": False,
    "auto_lock_vertical_center_on_pregrasp": False,
    "auto_lock_vertical_center_offset_ratio": 0.10,
    "approach_step": 1.5,
    "approach_max_command_lead": 4.0,
    "approach_profile_max_lift_delta": 34.0,
    "approach_profile_elbow_delta": 12.3,
    "approach_profile_wrist_delta": -54.0,
    "approach_profile_wrist_trim": 0.0,
    "visual_servo_max_joint_step": 6.0,
    "visual_handoff_enabled": True,
    "visual_handoff_bbox_height_ratio": 0.85,
    "visual_handoff_area_ratio": 0.18,
    "visual_handoff_tof_m": 0.19,
    "visual_handoff_max_tof_m": 0.21,
    "final_grasp_tof_m": 0.090,
    "final_approach_step": 1.0,
    "final_approach_max_command_lead": 4.0,
    "final_approach_max_lift_delta": 20.0,
    "final_approach_command_interval_sec": 0.10,
    "final_approach_timeout_sec": 6.0,
    "final_approach_endpoint_settle_sec": 0.75,
    "centering_threshold": 0.04,
    "centering_limit_hold_cycles": 3,
    "centering_error_window": 3,
    "centering_min_samples": 2,
    "centering_confirm_cycles": 2,
    "centering_step_limit": 1.5,
    "centering_min_step_limit": 0.25,
    "centering_slow_zone": 0.12,
    "centering_max_command_lead": 4.0,
    "centering_command_interval_sec": 0.08,
    "grasp_area_threshold": 0.45,
    "gripper_open": 70.0,
    "gripper_close": 0.0,
    "move_fps": 15.0,
    "arrive_threshold": 2.0,
    "elbow_arrive_threshold": 5.0,
    "arrive_stable_margin": 0.75,
    "arrive_stable_delta": 0.35,
    "arrive_stable_cycles": 5,
    "move_step_limit": 3.0,
    "move_timeout_sec": 15.0,
    "grasp_timeout_sec": 20.0,
    "joint_command_limit": 95.0,
    "preset_joint_limit": 95.0,
    "standby_joint_limit": 99.5,
    "center_offset_x": 143.0,
    "center_offset_y": 61.0,
    "detection_anchor_x_ratio": 0.5,
    "detection_anchor_y_ratio": 0.5,
    "tof_enabled": False,
    "tof_control_enabled": False,
    "tof_calibrated": False,
    "tof_topic": "/visual_grasp/tof_range",
    "tof_stale_timeout_sec": 0.25,
    "tof_filter_window": 5,
    "tof_min_valid_samples": 3,
    "tof_grasp_distance_m": 0.06,
    "runtime_config_path": "~/.config/project_link/visual_grasp/overrides.yaml",
    "runtime_positions_path": "~/.config/project_link/visual_grasp/positions.json",
    "action_default_timeout_sec": 45.0,
}
PERSISTED_PARAMETERS = set(PARAMETER_DEFAULTS) - {
    "robot_namespace",
    "runtime_config_path",
    "runtime_positions_path",
}


class VisualGraspNode(Node):
    def __init__(self) -> None:
        super().__init__("visual_grasp")
        for name, default in PARAMETER_DEFAULTS.items():
            self.declare_parameter(name, default)
        self._values = {name: self.get_parameter(name).value for name in PARAMETER_DEFAULTS}
        self._runtime = RuntimeStore(
            str(self._values["runtime_config_path"]),
            str(self._values["runtime_positions_path"]),
        )
        self._apply_runtime_overrides()
        self.add_on_set_parameters_callback(self._on_parameters_set)

        self._host = socket.gethostname()
        self._ip = self._local_ip()
        self._camera: Any = None
        self._native_process: subprocess.Popen[bytes] | None = None
        self._capture_thread: threading.Thread | None = None
        self._capture_stop = threading.Event()
        self._frame_lock = threading.Lock()
        self._latest_frame: Any = None
        self._latest_jpeg: bytes | None = None
        self._latest_capture_sequence = 0
        self._latest_capture_monotonic = 0.0
        self._capture_times: deque[float] = deque(maxlen=120)
        self._last_tracking_sequence = 0
        self._last_preview_sequence = 0
        self._last_preview_time = 0.0
        self._capture_mode = "starting"
        self._camera_ready = False
        self._frame_size = (int(self._values["camera_width"]), int(self._values["camera_height"]))
        self._last_detection: Detection | None = None
        self._last_message = "Starting"
        self._tof_samples = deque(maxlen=int(self._values["tof_filter_window"]))
        self._tof_last_monotonic: float | None = None
        self._tof_subscription = None

        self._arm = SO101Arm()
        if str(self._values["detector_backend"]) == "external_cuda":
            self._tracker = ExternalYoloWorldTracker(self)
        else:
            self._tracker = YoloWorldTracker(
                str(self._values["model_path"]),
                self._values,
            )
        self._tracker.update_config(self._values)
        self._controller = VisualServoController(
            self._arm,
            self._values,
            self._runtime.load_positions(),
        )
        self._action_group = ReentrantCallbackGroup()
        self._create_tof_subscription()
        self._open_camera()
        if bool(self._values["auto_connect_arm"]):
            self._connect_arm()

        image_qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT)
        self._image_pub = self.create_publisher(
            CompressedImage,
            "/visual_grasp/image/compressed",
            image_qos,
        )
        self._camera_status_pub = self.create_publisher(
            String,
            "/visual_grasp/camera_status",
            10,
        )
        self._status_pub = self.create_publisher(VisualGraspStatus, "/visual_grasp/status", 10)
        self._discovery_pub = self.create_publisher(
            VisualGraspStatus,
            "/project_link_visual_grasp/discovery",
            10,
        )
        self._create_services()
        self._action_server = ActionServer(
            self,
            TrackAndGrasp,
            "/visual_grasp/track_and_grasp",
            execute_callback=self._execute_track_and_grasp,
            goal_callback=self._accept_goal,
            cancel_callback=self._cancel_goal,
            callback_group=self._action_group,
        )
        period = 1.0 / max(float(self._values["move_fps"]), 1.0)
        self._tick_timer = self.create_timer(period, self._tick)
        preview_period = 0.5 / max(float(self._values["preview_fps"]), 1.0)
        self._preview_timer = self.create_timer(preview_period, self._publish_preview)
        self._status_timer = self.create_timer(0.5, self._publish_status)
        self._camera_status_timer = self.create_timer(1.0, self._publish_camera_status)
        self._calibration_timer = self.create_timer(0.05, self._arm.calibration_sample)
        self.get_logger().info("Visual grasp node started without GUI")

    def _apply_runtime_overrides(self) -> None:
        overrides = self._runtime.load_overrides()
        parameters = [
            Parameter(name, value=value)
            for name, value in overrides.items()
            if name in PERSISTED_PARAMETERS
        ]
        if parameters:
            self.set_parameters(parameters)
            self._values.update({parameter.name: parameter.value for parameter in parameters})

    def _on_parameters_set(self, parameters: list[Parameter]) -> SetParametersResult:
        for parameter in parameters:
            if parameter.name in {
                "camera_width",
                "camera_height",
                "camera_fps",
                "preview_fps",
                "camera_reopen_interval_sec",
                "detector_stale_timeout_sec",
                "jpeg_quality",
                "tof_filter_window",
                "tof_min_valid_samples",
            } and parameter.value <= 0:
                return SetParametersResult(successful=False, reason=f"{parameter.name} must be positive")
            if parameter.name in {"yolo_ema_alpha", "yolo_conf_threshold", "centering_threshold", "grasp_area_threshold"}:
                if not 0.0 <= float(parameter.value) <= 1.0:
                    return SetParametersResult(successful=False, reason=f"{parameter.name} must be between 0 and 1")
            if parameter.name in {
                "detection_anchor_x_ratio",
                "detection_anchor_y_ratio",
            } and not 0.0 <= float(parameter.value) <= 1.0:
                return SetParametersResult(
                    successful=False,
                    reason=f"{parameter.name} must be between 0 and 1",
                )
            if parameter.name == "detector_backend" and parameter.value not in {
                "external_cuda",
                "in_process",
            }:
                return SetParametersResult(
                    successful=False,
                    reason="detector_backend must be external_cuda or in_process",
                )
            if (
                parameter.name == "detector_backend"
                and hasattr(self, "_tracker")
                and parameter.value != self._values["detector_backend"]
            ):
                return SetParametersResult(
                    successful=False,
                    reason="detector_backend change requires a service restart",
                )
            if parameter.name in {"tof_stale_timeout_sec", "tof_grasp_distance_m"}:
                if float(parameter.value) <= 0.0:
                    return SetParametersResult(successful=False, reason=f"{parameter.name} must be positive")
            if parameter.name == "auto_lock_vertical_center_offset_ratio":
                if not -0.25 <= float(parameter.value) <= 0.40:
                    return SetParametersResult(
                        successful=False,
                        reason="auto_lock_vertical_center_offset_ratio must be between -0.25 and 0.40",
                    )
            if parameter.name == "approach_profile_wrist_trim":
                if not -10.0 <= float(parameter.value) <= 10.0:
                    return SetParametersResult(
                        successful=False,
                        reason="approach_profile_wrist_trim must be between -10 and 10",
                    )
            if parameter.name in {
                "approach_profile_max_lift_delta",
                "visual_servo_max_joint_step",
                "visual_handoff_max_tof_m",
                "final_grasp_tof_m",
                "final_approach_endpoint_settle_sec",
            } and float(parameter.value) <= 0.0:
                return SetParametersResult(
                    successful=False,
                    reason=f"{parameter.name} must be positive",
                )
        changed = {parameter.name: parameter.value for parameter in parameters if parameter.name in PARAMETER_DEFAULTS}
        proposed = dict(self._values)
        proposed.update(changed)
        if int(proposed["tof_min_valid_samples"]) > int(proposed["tof_filter_window"]):
            return SetParametersResult(
                successful=False,
                reason="tof_min_valid_samples cannot exceed tof_filter_window",
            )
        self._values.update(changed)
        if hasattr(self, "_tracker"):
            self._tracker.update_config(changed)
            self._controller.update_config(changed)
            if {
                "center_offset_x",
                "center_offset_y",
                "detection_anchor_x_ratio",
                "detection_anchor_y_ratio",
            } & changed.keys():
                self._controller.use_configured_visual_center()
            if {
                "camera_device",
                "camera_width",
                "camera_height",
                "camera_fps",
                "prefer_native_mjpeg",
            } & changed.keys():
                self._reopen_camera()
            if "preview_fps" in changed and hasattr(self, "_preview_timer"):
                self.destroy_timer(self._preview_timer)
                preview_period = 0.5 / max(float(self._values["preview_fps"]), 1.0)
                self._preview_timer = self.create_timer(
                    preview_period,
                    self._publish_preview,
                )
            if "tof_filter_window" in changed:
                existing = list(self._tof_samples)
                self._tof_samples = deque(
                    existing[-int(self._values["tof_filter_window"]):],
                    maxlen=int(self._values["tof_filter_window"]),
                )
            if "tof_topic" in changed:
                self._create_tof_subscription()
        persisted = {name: self._values[name] for name in PERSISTED_PARAMETERS}
        try:
            self._runtime.save_overrides(persisted)
        except OSError as exc:
            return SetParametersResult(successful=False, reason=f"Unable to persist parameters: {exc}")
        return SetParametersResult(successful=True, reason="Parameters applied and saved on Orin")

    def _create_tof_subscription(self) -> None:
        if self._tof_subscription is not None:
            self.destroy_subscription(self._tof_subscription)
        tof_qos = QoSProfile(depth=5, reliability=ReliabilityPolicy.BEST_EFFORT)
        self._tof_subscription = self.create_subscription(
            Range,
            str(self._values["tof_topic"]),
            self._on_tof_range,
            tof_qos,
        )

    def _on_tof_range(self, message: Range) -> None:
        value = float(message.range)
        if not message.min_range <= value <= message.max_range:
            return
        now = time.monotonic()
        if (
            self._tof_last_monotonic is not None
            and now - self._tof_last_monotonic
            > float(self._values["tof_stale_timeout_sec"])
        ):
            self._tof_samples.clear()
        self._tof_samples.append(value)
        self._tof_last_monotonic = now

    def _current_tof_reading(self) -> TofReading:
        if not bool(self._values["tof_enabled"]):
            return TofReading(None, float("inf"), False, "disabled")
        if self._tof_last_monotonic is None:
            return TofReading(None, float("inf"), False, "no_range")

        age_sec = max(0.0, time.monotonic() - self._tof_last_monotonic)
        if age_sec > float(self._values["tof_stale_timeout_sec"]):
            return TofReading(None, age_sec, False, "stale")
        if len(self._tof_samples) < int(self._values["tof_min_valid_samples"]):
            return TofReading(None, age_sec, False, "insufficient_samples")
        return TofReading(float(median(self._tof_samples)), age_sec, True, "valid")

    def _tof_decision(self, reading: TofReading) -> str:
        if not bool(self._values["tof_enabled"]):
            return "DISABLED"
        if not reading.valid:
            return "HOLD" if bool(self._values["tof_control_enabled"]) else "INVALID"
        threshold_name = (
            "final_grasp_tof_m"
            if self._controller.state == ServoState.FINAL_APPROACH
            else "tof_grasp_distance_m"
        )
        if reading.range_m is not None and reading.range_m <= float(
            self._values[threshold_name]
        ):
            return "GRASP" if bool(self._values["tof_control_enabled"]) else "WOULD_GRASP"
        return "APPROACH" if bool(self._values["tof_control_enabled"]) else "OBSERVE"

    def _create_services(self) -> None:
        self.create_service(SetTarget, "/visual_grasp/set_target", self._set_target)
        self.create_service(SetGripper, "/visual_grasp/set_gripper", self._set_gripper)
        self.create_service(Trigger, "/visual_grasp/connect_arm", self._connect_arm_service)
        self.create_service(Trigger, "/visual_grasp/disconnect_arm", self._disconnect_arm_service)
        self.create_service(SetBool, "/visual_grasp/set_torque", self._set_torque)
        self.create_service(Trigger, "/visual_grasp/start_approach", self._start_approach)
        self.create_service(
            Trigger,
            "/visual_grasp/start_visual_servo",
            self._start_visual_servo,
        )
        self.create_service(Trigger, "/visual_grasp/stop", self._stop)
        self.create_service(Trigger, "/visual_grasp/record_standby", self._record_position("standby"))
        self.create_service(Trigger, "/visual_grasp/record_pregrasp", self._record_position("pregrasp"))
        self.create_service(Trigger, "/visual_grasp/record_placement", self._record_position("placement"))
        self.create_service(Trigger, "/visual_grasp/go_standby", self._go_position("standby"))
        self.create_service(Trigger, "/visual_grasp/go_pregrasp", self._go_position("pregrasp"))
        self.create_service(Trigger, "/visual_grasp/go_placement", self._go_position("placement"))
        self.create_service(Trigger, "/visual_grasp/start_demo_recording", self._start_demo)
        self.create_service(Trigger, "/visual_grasp/stop_demo_recording", self._stop_demo)
        self.create_service(Trigger, "/visual_grasp/calibration_start", self._calibration_start)
        self.create_service(
            Trigger,
            "/visual_grasp/calibration_set_middle",
            self._calibration_set_middle,
        )
        self.create_service(
            Trigger,
            "/visual_grasp/calibration_finish",
            self._calibration_finish,
        )
        self.create_service(
            Trigger,
            "/visual_grasp/calibration_cancel",
            self._calibration_cancel,
        )

    def _set_target(self, request: SetTarget.Request, response: SetTarget.Response) -> SetTarget.Response:
        response.success, response.message = self._tracker.set_target(request.target)
        if response.success:
            self._last_detection = None
            self._controller.set_tracking()
        return response

    def _set_gripper(self, request: SetGripper.Request, response: SetGripper.Response) -> SetGripper.Response:
        response.success, response.message = self._arm.set_gripper(request.position)
        return response

    def _connect_arm_service(self, _request: Trigger.Request, response: Trigger.Response) -> Trigger.Response:
        response.success, response.message = self._connect_arm()
        return response

    def _disconnect_arm_service(self, _request: Trigger.Request, response: Trigger.Response) -> Trigger.Response:
        operation = (
            self._arm.cancel_calibration
            if self._arm.calibration_active
            else self._arm.disconnect
        )
        response.success, response.message = operation()
        return response

    def _connect_arm(self) -> tuple[bool, str]:
        return self._arm.connect(str(self._values["robot_port"]), str(self._values["robot_id"]))

    def _set_torque(self, request: SetBool.Request, response: SetBool.Response) -> SetBool.Response:
        operation = self._arm.enable_torque if request.data else self._arm.disable_torque
        response.success, response.message = operation()
        return response

    def _start_approach(self, _request: Trigger.Request, response: Trigger.Response) -> Trigger.Response:
        self._last_detection = self._tracker.submit()
        if self._last_detection is None or not self._last_detection.trusted:
            response.success = False
            response.message = "Wait for a stable CUDA detection before grasping"
            return response
        response.success, response.message = self._controller.start_approach()
        return response

    def _start_visual_servo(
        self,
        _request: Trigger.Request,
        response: Trigger.Response,
    ) -> Trigger.Response:
        self._last_detection = self._tracker.submit()
        if not self._tracker.model_ready:
            response.success = False
            response.message = "CUDA YOLO-World detector is not ready"
            return response
        if not self._tracker.target:
            response.success = False
            response.message = "Select and start tracking a target first"
            return response
        if self._last_detection is None or not self._last_detection.trusted:
            response.success = False
            response.message = "Wait for a stable green detection anchor first"
            return response
        response.success, response.message = self._controller.start_visual_servo()
        return response

    def _stop(self, _request: Trigger.Request, response: Trigger.Response) -> Trigger.Response:
        response.success, response.message = self._controller.stop()
        return response

    def _record_position(self, name: str):
        def callback(_request: Trigger.Request, response: Trigger.Response) -> Trigger.Response:
            response.success, response.message = self._controller.record_position(name)
            if response.success:
                self._runtime.save_positions(self._controller.positions)
            return response
        return callback

    def _go_position(self, name: str):
        def callback(_request: Trigger.Request, response: Trigger.Response) -> Trigger.Response:
            response.success, response.message = self._controller.go_to_position(name)
            return response
        return callback

    def _start_demo(self, _request: Trigger.Request, response: Trigger.Response) -> Trigger.Response:
        self._controller.stop()
        disabled, message = self._arm.disable_torque()
        if not disabled:
            response.success = False
            response.message = message
            return response
        self._controller.demo_rows.clear()
        self._controller.demo_recording = True
        response.success = True
        response.message = "Demo recording started; controller motion and torque are disabled"
        return response

    def _stop_demo(self, _request: Trigger.Request, response: Trigger.Response) -> Trigger.Response:
        self._controller.demo_recording = False
        self._arm.enable_torque()
        output_dir = Path(str(self._values["runtime_positions_path"])).expanduser().parent / "demos"
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / f"demo_{int(time.time())}.csv"
        rows = self._controller.demo_rows
        with output.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=["time", "state", "bbox", "confidence", "joints"])
            writer.writeheader()
            writer.writerows(rows)
        response.success = True
        response.message = f"Demo recording saved to {output}"
        return response

    def _calibration_start(
        self,
        _request: Trigger.Request,
        response: Trigger.Response,
    ) -> Trigger.Response:
        self._controller.stop()
        response.success, response.message = self._arm.start_calibration(
            str(self._values["robot_port"]),
            str(self._values["robot_id"]),
        )
        return response

    def _calibration_set_middle(
        self,
        _request: Trigger.Request,
        response: Trigger.Response,
    ) -> Trigger.Response:
        response.success, response.message = self._arm.calibration_set_middle()
        return response

    def _calibration_finish(
        self,
        _request: Trigger.Request,
        response: Trigger.Response,
    ) -> Trigger.Response:
        response.success, response.message = self._arm.finish_calibration()
        if response.success:
            self._controller.positions.clear()
            self._runtime.save_positions(self._controller.positions)
            response.message += "; saved poses were cleared and must be recorded again"
        return response

    def _calibration_cancel(
        self,
        _request: Trigger.Request,
        response: Trigger.Response,
    ) -> Trigger.Response:
        response.success, response.message = self._arm.cancel_calibration()
        return response

    def _accept_goal(self, _goal: TrackAndGrasp.Goal) -> GoalResponse:
        return GoalResponse.ACCEPT

    def _cancel_goal(self, _goal_handle) -> CancelResponse:
        return CancelResponse.ACCEPT

    def _execute_track_and_grasp(self, goal_handle):
        goal = goal_handle.request
        timeout = float(goal.timeout_sec) if goal.timeout_sec > 0.0 else float(self._values["action_default_timeout_sec"])
        result = TrackAndGrasp.Result()
        accepted, message = self._tracker.set_target(goal.target)
        if not accepted:
            goal_handle.abort()
            result.success = False
            result.final_state = "REJECTED"
            result.message = message
            return result
        self._last_detection = None
        self._controller.set_tracking()
        started_motion = False
        deadline = time.monotonic() + timeout
        while rclpy.ok() and time.monotonic() < deadline:
            feedback = TrackAndGrasp.Feedback()
            feedback.state = self._controller.state.value
            feedback.message = self._controller.message
            feedback.confidence = self._last_detection.confidence if self._last_detection else 0.0
            goal_handle.publish_feedback(feedback)
            if goal_handle.is_cancel_requested:
                self._controller.stop()
                goal_handle.canceled()
                result.success = False
                result.final_state = "CANCELED"
                result.message = "Track-and-grasp action canceled"
                return result
            if not started_motion and self._last_detection is not None:
                accepted, message = self._arm.set_gripper(
                    float(self._values["gripper_open"])
                )
                if accepted:
                    accepted, message = self._controller.start_grasp_sequence()
                if not accepted:
                    goal_handle.abort()
                    result.success = False
                    result.final_state = "HARDWARE_ERROR"
                    result.message = message
                    return result
                started_motion = True
            if self._controller.state == ServoState.GRASPED:
                goal_handle.succeed()
                result.success = True
                result.final_state = ServoState.GRASPED.value
                result.message = self._controller.message
                return result
            if self._controller.state == ServoState.ERROR:
                goal_handle.abort()
                result.success = False
                result.final_state = ServoState.ERROR.value
                result.message = self._controller.message
                return result
            time.sleep(0.1)
        self._controller.stop()
        goal_handle.abort()
        result.success = False
        tof_reading = self._current_tof_reading()
        if started_motion and bool(self._values["tof_control_enabled"]) and not tof_reading.valid:
            result.final_state = "RANGE_STALE"
        else:
            result.final_state = "TIMEOUT" if started_motion else "TARGET_NOT_FOUND"
        result.message = "Timed out waiting for a grasp result"
        return result

    def _open_camera(self) -> None:
        stop_event = threading.Event()
        self._capture_stop = stop_event
        self._camera_ready = False
        self._capture_mode = "starting"
        self._capture_thread = threading.Thread(
            target=self._capture_loop,
            args=(stop_event,),
            name="visual-grasp-camera",
            daemon=True,
        )
        self._capture_thread.start()

    def _capture_loop(self, stop_event: threading.Event) -> None:
        native_allowed = bool(self._values["prefer_native_mjpeg"])
        native_allowed = native_allowed and shutil.which("v4l2-ctl") is not None
        if native_allowed:
            try:
                self._native_mjpeg_loop(stop_event)
                return
            except Exception as exc:
                if not stop_event.is_set():
                    self.get_logger().warning(
                        f"Native arm-camera MJPEG failed; using OpenCV fallback: {exc}"
                    )
        if not stop_event.is_set():
            self._decoded_capture_loop(stop_event)

    def _native_mjpeg_loop(self, stop_event: threading.Event) -> None:
        device = str(self._values["camera_device"])
        width = int(self._values["camera_width"])
        height = int(self._values["camera_height"])
        fps = float(self._values["camera_fps"])
        process = subprocess.Popen(
            native_mjpeg_command(device, width, height, fps),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        self._native_process = process
        if process.stdout is None:
            raise RuntimeError("v4l2-ctl did not expose the arm-camera stream")
        self._capture_mode = "native_mjpeg"
        self.get_logger().info(
            f"Opened camera {device} as native MJPEG {width}x{height} @ {fps:.1f} FPS"
        )
        buffer = bytearray()
        while not stop_event.is_set():
            chunk = process.stdout.read(65536)
            if not chunk:
                if process.poll() is not None:
                    raise RuntimeError(f"v4l2-ctl exited with {process.returncode}")
                continue
            buffer.extend(chunk)
            while True:
                jpeg = pop_native_jpeg(buffer)
                if jpeg is None:
                    break
                self._store_capture(jpeg=jpeg, frame=None, width=width, height=height)

    def _open_decoded_camera(self) -> bool:
        try:
            import cv2

            device = str(self._values["camera_device"])
            camera = cv2.VideoCapture(device, cv2.CAP_V4L2)
            camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            camera.set(cv2.CAP_PROP_FRAME_WIDTH, int(self._values["camera_width"]))
            camera.set(cv2.CAP_PROP_FRAME_HEIGHT, int(self._values["camera_height"]))
            camera.set(cv2.CAP_PROP_FPS, float(self._values["camera_fps"]))
            if not camera.isOpened():
                camera.release()
                return False
            self._camera = camera
            self._capture_mode = "opencv_mjpeg"
            width = int(camera.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(camera.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fps = float(camera.get(cv2.CAP_PROP_FPS))
            self.get_logger().info(
                f"Opened camera {device} through OpenCV MJPEG {width}x{height} @ {fps:.1f} FPS"
            )
            return True
        except Exception as exc:
            self._camera = None
            self._last_message = f"Camera unavailable: {exc}"
            self.get_logger().error(self._last_message)
            return False

    def _decoded_capture_loop(self, stop_event: threading.Event) -> None:
        retry = max(0.1, float(self._values["camera_reopen_interval_sec"]))
        while not stop_event.is_set():
            if self._camera is None and not self._open_decoded_camera():
                stop_event.wait(retry)
                continue
            ok, frame = self._camera.read()
            if not ok or frame is None:
                self._camera.release()
                self._camera = None
                self._camera_ready = False
                self._last_message = "Camera frame read failed; reopening"
                continue
            height, width = frame.shape[:2]
            self._store_capture(jpeg=None, frame=frame, width=width, height=height)

    def _store_capture(self, jpeg, frame, width: int, height: int) -> None:
        with self._frame_lock:
            self._latest_jpeg = jpeg
            self._latest_frame = frame
            self._latest_capture_sequence += 1
            self._latest_capture_monotonic = time.monotonic()
            self._capture_times.append(self._latest_capture_monotonic)
            self._frame_size = (int(width), int(height))
        self._camera_ready = True

    def _stop_camera(self) -> None:
        self._capture_stop.set()
        process = self._native_process
        if process is not None and process.poll() is None:
            process.terminate()
        if self._camera is not None:
            self._camera.release()
            self._camera = None
        thread = self._capture_thread
        if thread is not None:
            thread.join(timeout=1.5)
        if process is not None and process.poll() is None:
            process.kill()
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        self._native_process = None
        self._capture_thread = None
        self._camera_ready = False

    def _reopen_camera(self) -> None:
        self._stop_camera()
        with self._frame_lock:
            self._latest_frame = None
            self._latest_jpeg = None
            self._latest_capture_sequence = 0
            self._latest_capture_monotonic = 0.0
            self._capture_times.clear()
            self._last_tracking_sequence = 0
            self._last_preview_sequence = 0
        self._open_camera()

    def _tick(self) -> None:
        if not self._camera_ready:
            return
        if self._tracker.target:
            if bool(getattr(self._tracker, "requires_frame", True)):
                with self._frame_lock:
                    sequence = self._latest_capture_sequence
                    jpeg = self._latest_jpeg
                    frame = (
                        None
                        if self._latest_frame is None
                        else self._latest_frame.copy()
                    )
                if sequence != self._last_tracking_sequence:
                    if frame is None and jpeg is not None:
                        try:
                            import cv2
                            import numpy as np

                            frame = cv2.imdecode(
                                np.frombuffer(jpeg, dtype=np.uint8),
                                cv2.IMREAD_COLOR,
                            )
                        except Exception as exc:
                            self._last_message = f"Tracking frame decode failed: {exc}"
                    if frame is not None:
                        height, width = frame.shape[:2]
                        self._frame_size = (width, height)
                        self._last_detection = self._tracker.submit(frame)
                        self._last_tracking_sequence = sequence
            else:
                self._last_detection = self._tracker.submit()
        self._controller.update(
            self._last_detection,
            self._frame_size,
            self._current_tof_reading(),
        )

    def _publish_preview(self) -> None:
        now = time.monotonic()
        preview_fps = max(float(self._values["preview_fps"]), 1.0)
        camera_fps = max(float(self._values["camera_fps"]), 1.0)
        if preview_fps < camera_fps:
            minimum_interval = 0.9 / preview_fps
            if now - self._last_preview_time < minimum_interval:
                return
        with self._frame_lock:
            sequence = self._latest_capture_sequence
            if sequence == 0 or sequence == self._last_preview_sequence:
                return
            jpeg = self._latest_jpeg
            frame = None if self._latest_frame is None else self._latest_frame.copy()
        if jpeg is None and frame is None:
            return
        try:
            if jpeg is None:
                import cv2

                ok, encoded = cv2.imencode(
                    ".jpg",
                    frame,
                    [int(cv2.IMWRITE_JPEG_QUALITY), int(self._values["jpeg_quality"])],
                )
                if not ok:
                    raise RuntimeError("OpenCV JPEG encoding failed")
                jpeg = encoded.tobytes()
            message = CompressedImage()
            message.header.stamp = self.get_clock().now().to_msg()
            message.format = "jpeg"
            message.data = jpeg
            self._image_pub.publish(message)
            self._last_preview_sequence = sequence
            self._last_preview_time = now
        except Exception as exc:
            self._last_message = f"Preview encoding failed: {exc}"

    def _publish_camera_status(self) -> None:
        with self._frame_lock:
            times = list(self._capture_times)
            width, height = self._frame_size
            age = (
                time.monotonic() - self._latest_capture_monotonic
                if self._latest_capture_monotonic > 0.0
                else -1.0
            )
        capture_fps = 0.0
        if len(times) >= 2:
            capture_fps = (len(times) - 1) / max(0.001, times[-1] - times[0])
        message = String()
        message.data = json.dumps(
            {
                "ready": bool(self._camera_ready),
                "capture_mode": self._capture_mode,
                "width": int(width),
                "height": int(height),
                "capture_fps": capture_fps,
                "frame_age_sec": age,
            },
            ensure_ascii=False,
        )
        self._camera_status_pub.publish(message)

    def _publish_status(self) -> None:
        if (
            self._latest_capture_monotonic > 0.0
            and time.monotonic() - self._latest_capture_monotonic > 1.0
        ):
            self._camera_ready = False
        tof_reading = self._current_tof_reading()
        message = VisualGraspStatus()
        message.stamp = self.get_clock().now().to_msg()
        message.robot_namespace = str(self._values["robot_namespace"])
        message.hostname = self._host
        message.ipv4 = self._ip
        message.state = self._controller.state.value
        message.message = self._controller.message or self._tracker.message or self._last_message
        message.target = self._tracker.target
        message.model_ready = self._tracker.model_ready
        message.detector_backend = str(self._values["detector_backend"])
        message.detector_device = self._tracker.device
        message.detector_inference_ms = float(self._tracker.inference_ms)
        message.camera_ready = self._camera_ready
        message.arm_connected = self._arm.connected
        message.torque_enabled = self._arm.torque_enabled
        message.arm_calibrated = self._arm.calibrated
        message.calibration_state = self._arm.calibration_state
        message.calibration_message = self._arm.calibration_message
        message.image_width, message.image_height = self._frame_size
        if self._last_detection:
            x, y, width, height = self._last_detection.bbox
            message.bbox_x, message.bbox_y = x, y
            message.bbox_width, message.bbox_height = width, height
            message.confidence = self._last_detection.confidence
        joints = self._arm.get_joints()
        message.joint_names = list(ALL_JOINTS)
        message.joint_positions = [float(joints.get(name, 0.0)) for name in ALL_JOINTS]
        message.tof_enabled = bool(self._values["tof_enabled"])
        message.tof_control_enabled = bool(self._values["tof_control_enabled"])
        message.tof_ready = tof_reading.valid
        message.tof_range_m = float(tof_reading.range_m or 0.0)
        message.tof_age_sec = (
            float(tof_reading.age_sec) if tof_reading.age_sec != float("inf") else -1.0
        )
        message.tof_state = tof_reading.reason
        message.tof_decision = self._tof_decision(tof_reading)
        self._status_pub.publish(message)
        self._discovery_pub.publish(message)

    @staticmethod
    def _local_ip() -> str:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("8.8.8.8", 80))
                return str(sock.getsockname()[0])
        except OSError:
            return ""

    def destroy_node(self) -> bool:
        self._stop_camera()
        self._tracker.stop()
        self._arm.disconnect()
        self._action_server.destroy()
        return super().destroy_node()


def main(args=None) -> None:
    rclpy.init(args=args)
    node = VisualGraspNode()
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        executor.shutdown()
        node.destroy_node()
        rclpy.shutdown()
