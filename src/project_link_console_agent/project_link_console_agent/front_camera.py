"""Orin-owned front camera publisher for the Ubuntu operator console."""

from __future__ import annotations

import json
import threading
import time
from typing import Any

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from rclpy.time import Time
from sensor_msgs.msg import CompressedImage
from std_msgs.msg import String

from project_link_emergency_interfaces.srv import CaptureStill


class FrontCameraNode(Node):
    def __init__(self) -> None:
        super().__init__("project_link_front_camera")
        self.declare_parameter("camera_device", "/dev/project_link_front_camera")
        self.declare_parameter("camera_width", 1280)
        self.declare_parameter("camera_height", 720)
        self.declare_parameter("camera_fps", 30.0)
        self.declare_parameter("preview_fps", 30.0)
        self.declare_parameter("preview_width", 1280)
        self.declare_parameter("preview_height", 720)
        self.declare_parameter("jpeg_quality", 70)
        self.declare_parameter("still_jpeg_quality", 85)
        self.declare_parameter("max_still_age_sec", 0.5)
        self.declare_parameter("rotation_degrees", 0)
        self.declare_parameter("frame_id", "front_camera_optical_frame")
        self.declare_parameter("reopen_interval_sec", 2.0)

        self._cv2: Any = None
        self._camera: Any = None
        self._last_open_attempt = 0.0
        self._last_status = ""
        self._frames = 0
        self._frame_lock = threading.Lock()
        self._capture_stop = threading.Event()
        self._capture_thread: threading.Thread | None = None
        self._latest_frame: Any = None
        self._latest_frame_monotonic = 0.0
        self._latest_stamp_ns = 0
        self._last_published_stamp_ns = 0
        image_qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT)
        self._image_pub = self.create_publisher(
            CompressedImage,
            "/front_camera/image/compressed",
            image_qos,
        )
        self._status_pub = self.create_publisher(String, "/front_camera/status", 10)
        self.create_service(CaptureStill, "/front_camera/capture_still", self._capture_still)
        period = 1.0 / max(1.0, float(self.get_parameter("preview_fps").value))
        self.create_timer(period, self._publish_preview)
        self.create_timer(1.0, self._publish_periodic_status)
        self._open_camera()
        self._capture_thread = threading.Thread(
            target=self._capture_loop,
            name="front-camera-capture",
            daemon=True,
        )
        self._capture_thread.start()

    def _publish_status(self, state: str, detail: str = "") -> None:
        payload = json.dumps(
            {
                "state": state,
                "detail": detail,
                "device": str(self.get_parameter("camera_device").value),
                "frames": self._frames,
            },
            ensure_ascii=False,
        )
        if payload == self._last_status:
            return
        self._last_status = payload
        message = String()
        message.data = payload
        self._status_pub.publish(message)

    def _open_camera(self) -> bool:
        self._last_open_attempt = time.monotonic()
        try:
            import cv2

            self._cv2 = cv2
            device = str(self.get_parameter("camera_device").value)
            camera = cv2.VideoCapture(device, cv2.CAP_V4L2)
            camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            camera.set(cv2.CAP_PROP_FRAME_WIDTH, int(self.get_parameter("camera_width").value))
            camera.set(cv2.CAP_PROP_FRAME_HEIGHT, int(self.get_parameter("camera_height").value))
            camera.set(cv2.CAP_PROP_FPS, float(self.get_parameter("camera_fps").value))
            if not camera.isOpened():
                camera.release()
                self._publish_status("unavailable", "camera_open_failed")
                return False
            self._camera = camera
            self._publish_status("ready")
            width = int(camera.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(camera.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fps = float(camera.get(cv2.CAP_PROP_FPS))
            self.get_logger().info(
                f"Front camera ready on {device}: {width}x{height} @ {fps:.1f} FPS"
            )
            return True
        except Exception as exc:
            self._camera = None
            self._publish_status("fault", f"{type(exc).__name__}: {exc}")
            self.get_logger().error(f"Unable to open front camera: {exc}")
            return False

    def _rotate(self, frame):
        degrees = int(self.get_parameter("rotation_degrees").value) % 360
        if degrees == 90:
            return self._cv2.rotate(frame, self._cv2.ROTATE_90_CLOCKWISE)
        if degrees == 180:
            return self._cv2.rotate(frame, self._cv2.ROTATE_180)
        if degrees == 270:
            return self._cv2.rotate(frame, self._cv2.ROTATE_90_COUNTERCLOCKWISE)
        return frame

    def _capture_loop(self) -> None:
        while not self._capture_stop.is_set():
            if self._camera is None:
                retry = float(self.get_parameter("reopen_interval_sec").value)
                if time.monotonic() - self._last_open_attempt >= retry:
                    self._open_camera()
                self._capture_stop.wait(0.05)
                continue
            ok, frame = self._camera.read()
            if not ok or frame is None:
                self._camera.release()
                self._camera = None
                self._publish_status("unavailable", "frame_read_failed")
                continue
            frame = self._rotate(frame)
            stamp_ns = self.get_clock().now().nanoseconds
            with self._frame_lock:
                self._latest_frame = frame
                self._latest_frame_monotonic = time.monotonic()
                self._latest_stamp_ns = stamp_ns

    def _publish_preview(self) -> None:
        with self._frame_lock:
            if (
                self._latest_frame is None
                or self._latest_stamp_ns == self._last_published_stamp_ns
            ):
                return
            frame = self._latest_frame.copy()
            stamp_ns = self._latest_stamp_ns
            self._last_published_stamp_ns = stamp_ns
        preview_width = max(1, int(self.get_parameter("preview_width").value))
        preview_height = max(1, int(self.get_parameter("preview_height").value))
        if frame.shape[1] != preview_width or frame.shape[0] != preview_height:
            frame = self._cv2.resize(frame, (preview_width, preview_height), interpolation=self._cv2.INTER_AREA)
        quality = int(self.get_parameter("jpeg_quality").value)
        encoded_ok, encoded = self._cv2.imencode(
            ".jpg",
            frame,
            [int(self._cv2.IMWRITE_JPEG_QUALITY), max(35, min(90, quality))],
        )
        if not encoded_ok:
            self._publish_status("fault", "jpeg_encode_failed")
            return
        message = CompressedImage()
        message.header.stamp = Time(nanoseconds=stamp_ns).to_msg()
        message.header.frame_id = str(self.get_parameter("frame_id").value)
        message.format = "jpeg"
        message.data = encoded.tobytes()
        self._image_pub.publish(message)
        self._frames += 1

    def _capture_still(
        self,
        _request: CaptureStill.Request,
        response: CaptureStill.Response,
    ) -> CaptureStill.Response:
        with self._frame_lock:
            frame = None if self._latest_frame is None else self._latest_frame.copy()
            captured_at = self._latest_frame_monotonic
            stamp_ns = self._latest_stamp_ns
        if frame is None:
            response.success = False
            response.message = "front camera has no cached frame"
            return response
        age = time.monotonic() - captured_at
        if age > max(0.05, float(self.get_parameter("max_still_age_sec").value)):
            response.success = False
            response.message = f"front camera frame is stale: {age:.3f}s"
            return response
        quality = max(50, min(100, int(self.get_parameter("still_jpeg_quality").value)))
        encoded_ok, encoded = self._cv2.imencode(
            ".jpg",
            frame,
            [int(self._cv2.IMWRITE_JPEG_QUALITY), quality],
        )
        if not encoded_ok:
            response.success = False
            response.message = "failed to encode front camera still"
            return response
        height, width = frame.shape[:2]
        response.success = True
        response.message = "captured cached high-resolution front camera frame"
        response.jpeg_data = list(encoded.tobytes())
        response.width = int(width)
        response.height = int(height)
        response.stamp = Time(nanoseconds=stamp_ns).to_msg()
        return response

    def _publish_periodic_status(self) -> None:
        self._last_status = ""
        self._publish_status("streaming" if self._camera is not None else "unavailable")

    def destroy_node(self):
        self._capture_stop.set()
        if self._capture_thread is not None:
            self._capture_thread.join(timeout=1.5)
            self._capture_thread = None
        if self._camera is not None:
            self._camera.release()
            self._camera = None
        return super().destroy_node()


def main() -> None:
    rclpy.init()
    node = FrontCameraNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
