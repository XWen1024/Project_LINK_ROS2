#!/usr/bin/env python3
"""Second-camera JPEG still capture service for fall response."""

from __future__ import annotations

from typing import Any

import rclpy
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node

from project_link_emergency_interfaces.srv import CaptureStill


class FallCameraNode(Node):
    def __init__(self) -> None:
        super().__init__("fall_camera_node")
        self.declare_parameter("camera_device", "/dev/FallCam")
        self.declare_parameter("camera_width", 1280)
        self.declare_parameter("camera_height", 720)
        self.declare_parameter("camera_fps", 15.0)
        self.declare_parameter("jpeg_quality", 80)
        self.declare_parameter("warmup_frames", 2)
        self.declare_parameter("capture_service", "/fall_detection/capture_still")
        self._camera: Any = None
        self._cv2: Any = None
        self._open_camera()
        self.create_service(
            CaptureStill,
            str(self.get_parameter("capture_service").value),
            self._capture_still,
        )
        self.get_logger().info(
            f"Fall camera node ready on {self.get_parameter('camera_device').value}"
        )

    def _open_camera(self) -> bool:
        try:
            import cv2

            self._cv2 = cv2
            camera = cv2.VideoCapture(str(self.get_parameter("camera_device").value), cv2.CAP_V4L2)
            camera.set(cv2.CAP_PROP_FRAME_WIDTH, int(self.get_parameter("camera_width").value))
            camera.set(cv2.CAP_PROP_FRAME_HEIGHT, int(self.get_parameter("camera_height").value))
            camera.set(cv2.CAP_PROP_FPS, float(self.get_parameter("camera_fps").value))
            if not camera.isOpened():
                self.get_logger().warn("Fall camera could not be opened")
                self._camera = None
                return False
            self._camera = camera
            return True
        except Exception as exc:
            self.get_logger().error(f"Unable to open fall camera: {exc}")
            self._camera = None
            return False

    def _capture_still(
        self,
        _request: CaptureStill.Request,
        response: CaptureStill.Response,
    ) -> CaptureStill.Response:
        if self._camera is None and not self._open_camera():
            response.success = False
            response.message = "fall camera is unavailable"
            return response
        frame = None
        ok = False
        for _ in range(max(1, int(self.get_parameter("warmup_frames").value))):
            ok, frame = self._camera.read()
        if not ok or frame is None:
            response.success = False
            response.message = "fall camera did not return a frame"
            return response
        quality = int(self.get_parameter("jpeg_quality").value)
        encoded_ok, encoded = self._cv2.imencode(".jpg", frame, [int(self._cv2.IMWRITE_JPEG_QUALITY), quality])
        if not encoded_ok:
            response.success = False
            response.message = "failed to encode fall camera frame as JPEG"
            return response
        height, width = frame.shape[:2]
        response.success = True
        response.message = "captured"
        response.jpeg_data = list(encoded.tobytes())
        response.width = int(width)
        response.height = int(height)
        response.stamp = self.get_clock().now().to_msg()
        return response

    def destroy_node(self) -> bool:
        if self._camera is not None:
            self._camera.release()
        return super().destroy_node()


def main() -> None:
    rclpy.init()
    node = FallCameraNode()
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
