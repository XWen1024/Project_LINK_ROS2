"""Isolated CUDA YOLO-World process for visual grasping."""

from __future__ import annotations

import json
import threading

import rclpy
from rclpy.node import Node
from rclpy.qos import (
    DurabilityPolicy,
    QoSProfile,
    ReliabilityPolicy,
    qos_profile_sensor_data,
)
from sensor_msgs.msg import CompressedImage
from std_msgs.msg import String
from wheeltec_robot_msg.msg import VisualGraspDetection

from .core import YoloWorldTracker


class CudaYoloWorldDetector(Node):
    def __init__(self) -> None:
        super().__init__("visual_grasp_cuda_detector")
        self.declare_parameter("model_path", "/home/wte/models/yolov8s-worldv2.pt")
        self.declare_parameter("yolo_device", "cuda:0")
        self.declare_parameter("detector_submit_fps", 15.0)
        self.declare_parameter("yolo_conf_threshold", 0.15)
        self.declare_parameter("yolo_max_lost_frames", 15)
        self.declare_parameter("yolo_infer_interval_sec", 0.0)
        self.declare_parameter("yolo_ema_alpha", 0.6)
        self.declare_parameter("yolo_max_center_jump_ratio", 0.12)
        self.declare_parameter("yolo_max_area_change_ratio", 1.8)
        self.declare_parameter("yolo_outlier_hold_frames", 4)
        self.declare_parameter("yolo_track_iou_weight", 0.5)
        names = [
            "yolo_device",
            "yolo_conf_threshold",
            "yolo_max_lost_frames",
            "yolo_infer_interval_sec",
            "yolo_ema_alpha",
            "yolo_max_center_jump_ratio",
            "yolo_max_area_change_ratio",
            "yolo_outlier_hold_frames",
            "yolo_track_iou_weight",
        ]
        config = {name: self.get_parameter(name).value for name in names}
        self._tracker = YoloWorldTracker(
            str(self.get_parameter("model_path").value),
            config,
        )
        self._lock = threading.Lock()
        self._latest_jpeg: bytes | None = None
        self._latest_image_sequence = 0
        self._last_submitted_sequence = 0
        self._frame_size = (0, 0)
        self._requested_target = ""
        target_qos = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        self.create_subscription(
            String,
            "/visual_grasp/detector/target",
            self._on_target,
            target_qos,
        )
        self.create_subscription(
            String,
            "/visual_grasp/detector/config",
            self._on_config,
            target_qos,
        )
        self.create_subscription(
            CompressedImage,
            "/visual_grasp/image/compressed",
            self._on_image,
            qos_profile_sensor_data,
        )
        self._result_pub = self.create_publisher(
            VisualGraspDetection,
            "/visual_grasp/detector/result",
            10,
        )
        submit_fps = max(1.0, float(self.get_parameter("detector_submit_fps").value))
        self.create_timer(1.0 / submit_fps, self._tick)

    def _on_target(self, message: String) -> None:
        target = message.data.strip()
        with self._lock:
            self._requested_target = target
        if not target:
            self._tracker.clear_target()

    def _on_config(self, message: String) -> None:
        try:
            payload = json.loads(message.data)
        except (TypeError, ValueError):
            return
        if not isinstance(payload, dict):
            return
        allowed = {
            name: value
            for name, value in payload.items()
            if name.startswith("yolo_") and name != "yolo_device"
        }
        if allowed:
            self._tracker.update_config(allowed)

    def _on_image(self, message: CompressedImage) -> None:
        with self._lock:
            self._latest_jpeg = bytes(message.data)
            self._latest_image_sequence += 1

    def _tick(self) -> None:
        with self._lock:
            target = self._requested_target
            jpeg = self._latest_jpeg
            image_sequence = self._latest_image_sequence
        if target and self._tracker.model_ready and target != self._tracker.target:
            success, detail = self._tracker.set_target(target)
            if not success:
                self.get_logger().warning(detail)
        if (
            target
            and jpeg is not None
            and image_sequence != self._last_submitted_sequence
        ):
            try:
                import cv2
                import numpy as np

                frame = cv2.imdecode(
                    np.frombuffer(jpeg, dtype=np.uint8),
                    cv2.IMREAD_COLOR,
                )
            except Exception as exc:
                self.get_logger().warning(f"CUDA detector JPEG decode failed: {exc}")
                frame = None
            if frame is not None:
                self._frame_size = (int(frame.shape[1]), int(frame.shape[0]))
                self._tracker.submit(frame)
                self._last_submitted_sequence = image_sequence
        self._publish_result(self._tracker.submit(None))

    def _publish_result(self, detection) -> None:
        message = VisualGraspDetection()
        message.stamp = self.get_clock().now().to_msg()
        message.target = self._tracker.target
        message.model_ready = self._tracker.model_ready
        message.state = self._tracker.state.value
        message.message = self._tracker.message
        message.image_width, message.image_height = self._frame_size
        message.inference_ms = float(self._tracker.inference_ms)
        message.device = self._tracker.device
        if detection is not None:
            message.detection_present = True
            message.trusted = detection.trusted
            message.sequence = detection.sequence
            (
                message.bbox_x,
                message.bbox_y,
                message.bbox_width,
                message.bbox_height,
            ) = detection.bbox
            message.confidence = detection.confidence
        self._result_pub.publish(message)

    def destroy_node(self):
        self._tracker.stop()
        return super().destroy_node()


def main(args=None) -> None:
    rclpy.init(args=args)
    node = CudaYoloWorldDetector()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()
