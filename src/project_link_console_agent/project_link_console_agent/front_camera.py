"""Orin-owned front camera publisher for the Ubuntu operator console."""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from typing import Any

import rclpy
from rcl_interfaces.msg import SetParametersResult
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
        self.declare_parameter("preview_fps", 24.0)
        self.declare_parameter("preview_width", 1280)
        self.declare_parameter("preview_height", 720)
        self.declare_parameter("jpeg_quality", 70)
        self.declare_parameter("still_jpeg_quality", 85)
        self.declare_parameter("max_still_age_sec", 0.5)
        self.declare_parameter("rotation_degrees", 0)
        self.declare_parameter("frame_id", "front_camera_optical_frame")
        self.declare_parameter("reopen_interval_sec", 2.0)
        self.declare_parameter("prefer_native_mjpeg", True)
        self.declare_parameter("manual_exposure", True)
        self.declare_parameter("exposure_time_absolute", 300)
        self.declare_parameter("camera_gain", 48)
        self.declare_parameter("automatic_white_balance", True)
        self.declare_parameter("white_balance_temperature", 3400)

        self._cv2: Any = None
        self._camera: Any = None
        self._native_process: subprocess.Popen[bytes] | None = None
        self._last_open_attempt = 0.0
        self._last_status = ""
        self._frames = 0
        self._frame_lock = threading.Lock()
        self._capture_stop = threading.Event()
        self._capture_thread: threading.Thread | None = None
        self._latest_frame: Any = None
        self._latest_jpeg: bytes | None = None
        self._latest_frame_monotonic = 0.0
        self._latest_stamp_ns = 0
        self._last_published_stamp_ns = 0
        self._last_preview_publish_monotonic = 0.0
        self._capture_mode = "starting"
        self._exposure_update_requested = False
        self.add_on_set_parameters_callback(self._on_parameters_set)
        image_qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT)
        self._image_pub = self.create_publisher(
            CompressedImage,
            "/front_camera/image/compressed",
            image_qos,
        )
        self._status_pub = self.create_publisher(String, "/front_camera/status", 10)
        self.create_service(CaptureStill, "/front_camera/capture_still", self._capture_still)
        # Poll the latest-frame slot faster than capture. Only a new timestamp is
        # published, so this avoids clock aliasing without duplicating frames.
        period = 1.0 / max(1.0, float(self.get_parameter("preview_fps").value))
        self.create_timer(period, self._publish_preview)
        self.create_timer(1.0, self._publish_periodic_status)
        self.create_timer(0.1, self._apply_pending_exposure)
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
                "capture_mode": self._capture_mode,
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

    def _on_parameters_set(self, parameters) -> SetParametersResult:
        for parameter in parameters:
            try:
                if parameter.name == "manual_exposure" and not isinstance(
                    parameter.value, bool
                ):
                    return SetParametersResult(
                        successful=False, reason="manual_exposure must be boolean"
                    )
                if parameter.name == "exposure_time_absolute" and not 1 <= int(
                    parameter.value
                ) <= 5000:
                    return SetParametersResult(
                        successful=False,
                        reason="exposure_time_absolute must be between 1 and 5000",
                    )
                if parameter.name == "camera_gain" and not 0 <= int(
                    parameter.value
                ) <= 63:
                    return SetParametersResult(
                        successful=False, reason="camera_gain must be between 0 and 63"
                    )
                if parameter.name == "automatic_white_balance" and not isinstance(
                    parameter.value, bool
                ):
                    return SetParametersResult(
                        successful=False, reason="automatic_white_balance must be boolean"
                    )
                if parameter.name == "white_balance_temperature" and not 2800 <= int(
                    parameter.value
                ) <= 6500:
                    return SetParametersResult(
                        successful=False,
                        reason="white_balance_temperature must be between 2800 and 6500",
                    )
            except (TypeError, ValueError):
                return SetParametersResult(
                    successful=False, reason=f"invalid value for {parameter.name}"
                )
        if any(
            parameter.name
            in {
                "manual_exposure",
                "exposure_time_absolute",
                "camera_gain",
                "automatic_white_balance",
                "white_balance_temperature",
            }
            for parameter in parameters
        ):
            self._exposure_update_requested = True
        return SetParametersResult(successful=True)

    def _apply_pending_exposure(self) -> None:
        if not self._exposure_update_requested:
            return
        self._exposure_update_requested = False
        self._apply_exposure_controls(str(self.get_parameter("camera_device").value))

    def _capture_loop(self) -> None:
        native_allowed = bool(self.get_parameter("prefer_native_mjpeg").value)
        native_allowed = native_allowed and int(
            self.get_parameter("rotation_degrees").value
        ) % 360 == 0
        native_allowed = native_allowed and shutil.which("v4l2-ctl") is not None
        if native_allowed:
            try:
                self._native_mjpeg_loop()
                return
            except Exception as exc:
                if not self._capture_stop.is_set():
                    self.get_logger().warning(
                        f"Native MJPEG capture failed; falling back to OpenCV: {exc}"
                    )
        if not self._capture_stop.is_set():
            self._decoded_capture_loop()

    def _native_mjpeg_loop(self) -> None:
        device = str(self.get_parameter("camera_device").value)
        self._apply_exposure_controls(device)
        width = int(self.get_parameter("camera_width").value)
        height = int(self.get_parameter("camera_height").value)
        fps = float(self.get_parameter("camera_fps").value)
        command = [
            "v4l2-ctl",
            "-d",
            device,
            f"--set-fmt-video=width={width},height={height},pixelformat=MJPG",
            f"--set-parm={fps:g}",
            "--stream-mmap=3",
            "--stream-count=0",
            "--stream-to=-",
        ]
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        self._native_process = process
        if process.stdout is None:
            raise RuntimeError("v4l2-ctl did not expose a stream")
        self._capture_mode = "native_mjpeg"
        self._publish_status("ready", "native_mjpeg_zero_reencode")
        self.get_logger().info(
            f"Front camera native MJPEG ready on {device}: {width}x{height} @ {fps:.1f} FPS"
        )
        buffer = bytearray()
        while not self._capture_stop.is_set():
            chunk = process.stdout.read(65536)
            if not chunk:
                if process.poll() is not None:
                    raise RuntimeError(f"v4l2-ctl exited with {process.returncode}")
                continue
            buffer.extend(chunk)
            while True:
                jpeg = self._pop_native_jpeg(buffer)
                if jpeg is None:
                    break
                stamp_ns = self.get_clock().now().nanoseconds
                with self._frame_lock:
                    self._latest_jpeg = jpeg
                    self._latest_frame = None
                    self._latest_frame_monotonic = time.monotonic()
                    self._latest_stamp_ns = stamp_ns

    @staticmethod
    def _pop_native_jpeg(buffer: bytearray) -> bytes | None:
        """Pop one complete JPEG, dropping a truncated frame before a new SOI."""
        while True:
            start = buffer.find(b"\xff\xd8")
            if start < 0:
                if len(buffer) > 1:
                    del buffer[:-1]
                return None
            if start > 0:
                del buffer[:start]
                start = 0
            end = buffer.find(b"\xff\xd9", 2)
            next_start = buffer.find(b"\xff\xd8", 2)
            if next_start >= 0 and (end < 0 or next_start < end):
                # The UVC transfer for the previous frame was truncated. Keep
                # the newer SOI and wait for/publish only its matching EOI.
                del buffer[:next_start]
                continue
            if end < 0:
                if len(buffer) > 8 * 1024 * 1024:
                    buffer.clear()
                return None
            jpeg = bytes(buffer[: end + 2])
            del buffer[: end + 2]
            if len(jpeg) >= 1024:
                return jpeg

    def _apply_exposure_controls(self, device: str) -> None:
        if shutil.which("v4l2-ctl") is None:
            return
        if bool(self.get_parameter("manual_exposure").value):
            exposure = max(1, int(self.get_parameter("exposure_time_absolute").value))
            gain = max(0, int(self.get_parameter("camera_gain").value))
            controls = f"auto_exposure=1,exposure_time_absolute={exposure},gain={gain}"
        else:
            controls = "auto_exposure=3"
        try:
            result = subprocess.run(
                ["v4l2-ctl", "-d", device, f"--set-ctrl={controls}"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5.0,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            self.get_logger().warning(f"Unable to apply camera exposure controls: {exc}")
            return
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            self.get_logger().warning(f"Unable to apply camera exposure controls: {detail}")
            return
        automatic_white_balance = bool(
            self.get_parameter("automatic_white_balance").value
        )
        temperature = max(
            2800, min(6500, int(self.get_parameter("white_balance_temperature").value))
        )
        white_balance_commands = (
            ["white_balance_automatic=0", "white_balance_automatic=1"]
            if automatic_white_balance
            else [f"white_balance_automatic=0,white_balance_temperature={temperature}"]
        )
        for controls in white_balance_commands:
            result = subprocess.run(
                ["v4l2-ctl", "-d", device, f"--set-ctrl={controls}"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5.0,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout).strip()
                self.get_logger().warning(
                    f"Unable to apply camera white balance controls: {detail}"
                )
                break

    def _open_decoded_camera(self) -> bool:
        self._last_open_attempt = time.monotonic()
        try:
            import cv2

            self._cv2 = cv2
            device = str(self.get_parameter("camera_device").value)
            self._apply_exposure_controls(device)
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
            self._capture_mode = "opencv_fallback"
            self._publish_status("ready", "opencv_decode_reencode")
            width = int(camera.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(camera.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fps = float(camera.get(cv2.CAP_PROP_FPS))
            self.get_logger().info(
                f"Front camera OpenCV fallback on {device}: {width}x{height} @ {fps:.1f} FPS"
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

    def _decoded_capture_loop(self) -> None:
        while not self._capture_stop.is_set():
            if self._camera is None:
                retry = float(self.get_parameter("reopen_interval_sec").value)
                if time.monotonic() - self._last_open_attempt >= retry:
                    self._open_decoded_camera()
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
                self._latest_jpeg = None
                self._latest_frame_monotonic = time.monotonic()
                self._latest_stamp_ns = stamp_ns

    def _publish_preview(self) -> None:
        now = time.monotonic()
        with self._frame_lock:
            if self._latest_stamp_ns == self._last_published_stamp_ns:
                return
            frame = None if self._latest_frame is None else self._latest_frame.copy()
            jpeg = self._latest_jpeg
            stamp_ns = self._latest_stamp_ns
            self._last_published_stamp_ns = stamp_ns
        if jpeg is None and frame is None:
            return
        if jpeg is None:
            preview_width = max(1, int(self.get_parameter("preview_width").value))
            preview_height = max(1, int(self.get_parameter("preview_height").value))
            if frame.shape[1] != preview_width or frame.shape[0] != preview_height:
                frame = self._cv2.resize(
                    frame,
                    (preview_width, preview_height),
                    interpolation=self._cv2.INTER_AREA,
                )
            quality = int(self.get_parameter("jpeg_quality").value)
            encoded_ok, encoded = self._cv2.imencode(
                ".jpg",
                frame,
                [int(self._cv2.IMWRITE_JPEG_QUALITY), max(35, min(90, quality))],
            )
            if not encoded_ok:
                self._publish_status("fault", "jpeg_encode_failed")
                return
            jpeg = encoded.tobytes()
        message = CompressedImage()
        message.header.stamp = Time(nanoseconds=stamp_ns).to_msg()
        message.header.frame_id = str(self.get_parameter("frame_id").value)
        message.format = "jpeg"
        message.data = jpeg
        self._image_pub.publish(message)
        self._last_preview_publish_monotonic = now
        self._frames += 1

    def _capture_still(
        self,
        _request: CaptureStill.Request,
        response: CaptureStill.Response,
    ) -> CaptureStill.Response:
        with self._frame_lock:
            frame = None if self._latest_frame is None else self._latest_frame.copy()
            jpeg = self._latest_jpeg
            captured_at = self._latest_frame_monotonic
            stamp_ns = self._latest_stamp_ns
        if jpeg is None and frame is None:
            response.success = False
            response.message = "front camera has no cached frame"
            return response
        age = time.monotonic() - captured_at
        if age > max(0.05, float(self.get_parameter("max_still_age_sec").value)):
            response.success = False
            response.message = f"front camera frame is stale: {age:.3f}s"
            return response
        if jpeg is None:
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
            jpeg = encoded.tobytes()
            height, width = frame.shape[:2]
        else:
            width = int(self.get_parameter("camera_width").value)
            height = int(self.get_parameter("camera_height").value)
        response.success = True
        response.message = "captured cached high-resolution front camera frame"
        response.jpeg_data = list(jpeg)
        response.width = int(width)
        response.height = int(height)
        response.stamp = Time(nanoseconds=stamp_ns).to_msg()
        return response

    def _publish_periodic_status(self) -> None:
        self._last_status = ""
        age = time.monotonic() - self._latest_frame_monotonic
        self._publish_status("streaming" if age < 1.0 else "unavailable")

    def destroy_node(self):
        self._capture_stop.set()
        process = self._native_process
        if process is not None and process.poll() is None:
            process.terminate()
        if self._capture_thread is not None:
            self._capture_thread.join(timeout=1.5)
            self._capture_thread = None
        if process is not None and process.poll() is None:
            process.kill()
        self._native_process = None
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
