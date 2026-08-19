"""ROS adapter for the isolated CUDA YOLO-World detector process."""

from __future__ import annotations

import json
import threading
import time

from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import String
from wheeltec_robot_msg.msg import VisualGraspDetection

from .core import Detection


class ExternalYoloWorldTracker:
    """Expose the detector process through the legacy tracker API."""

    requires_frame = False

    def __init__(self, node) -> None:
        self._lock = threading.Lock()
        self._target = ""
        self._model_ready = False
        self._message = "Waiting for CUDA YOLO-World detector"
        self._latest_detection: Detection | None = None
        self._device = "cuda:unknown"
        self._inference_ms = 0.0
        self._last_result_monotonic = 0.0
        self._stale_timeout_sec = float(
            node.get_parameter("detector_stale_timeout_sec").value
        )
        target_qos = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        self._target_pub = node.create_publisher(
            String,
            "/visual_grasp/detector/target",
            target_qos,
        )
        self._config_pub = node.create_publisher(
            String,
            "/visual_grasp/detector/config",
            target_qos,
        )
        self._detection_sub = node.create_subscription(
            VisualGraspDetection,
            "/visual_grasp/detector/result",
            self._on_detection,
            10,
        )

    @property
    def target(self) -> str:
        with self._lock:
            return self._target

    @property
    def model_ready(self) -> bool:
        with self._lock:
            self._mark_stale_locked()
            return self._model_ready

    @property
    def message(self) -> str:
        with self._lock:
            self._mark_stale_locked()
            return self._message

    @property
    def device(self) -> str:
        with self._lock:
            return self._device

    @property
    def inference_ms(self) -> float:
        with self._lock:
            return self._inference_ms

    def update_config(self, config: dict) -> None:
        if "detector_stale_timeout_sec" in config:
            with self._lock:
                self._stale_timeout_sec = float(
                    config["detector_stale_timeout_sec"]
                )
        detector_config = {
            name: value
            for name, value in config.items()
            if name.startswith("yolo_")
        }
        if detector_config:
            message = String()
            message.data = json.dumps(detector_config)
            self._config_pub.publish(message)

    def set_target(self, target: str) -> tuple[bool, str]:
        target = target.strip()
        if not target:
            return False, "Target text cannot be empty"
        with self._lock:
            self._target = target
            self._latest_detection = None
            ready = self._model_ready
            self._message = f"Requested CUDA tracking for {target}"
        message = String()
        message.data = target
        self._target_pub.publish(message)
        if ready:
            return True, f"CUDA YOLO-World is tracking {target}"
        return True, f"Target {target} queued while CUDA YOLO-World loads"

    def clear_target(self) -> None:
        with self._lock:
            self._target = ""
            self._latest_detection = None
        self._target_pub.publish(String(data=""))

    def submit(self, _frame=None) -> Detection | None:
        with self._lock:
            self._mark_stale_locked()
            return self._latest_detection

    def stop(self) -> None:
        return

    def _mark_stale_locked(self) -> None:
        if (
            self._last_result_monotonic <= 0.0
            or time.monotonic() - self._last_result_monotonic
            > self._stale_timeout_sec
        ):
            self._model_ready = False
            self._message = "CUDA YOLO-World detector is unavailable or stale"
            self._latest_detection = None

    def _on_detection(self, message: VisualGraspDetection) -> None:
        with self._lock:
            self._last_result_monotonic = time.monotonic()
            self._model_ready = bool(message.model_ready)
            self._message = message.message
            self._device = message.device or "cuda:unknown"
            self._inference_ms = float(message.inference_ms)
            if message.target != self._target or not message.detection_present:
                self._latest_detection = None
                return
            self._latest_detection = Detection(
                bbox=(
                    int(message.bbox_x),
                    int(message.bbox_y),
                    int(message.bbox_width),
                    int(message.bbox_height),
                ),
                confidence=float(message.confidence),
                trusted=bool(message.trusted),
                sequence=int(message.sequence),
            )
