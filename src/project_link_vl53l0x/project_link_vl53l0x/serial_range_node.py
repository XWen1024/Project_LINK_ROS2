"""ROS 2 serial owner for the ESP32-C3 VL53L0X bridge."""
from __future__ import annotations

import json
import threading
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import Range
from std_msgs.msg import String

from .protocol import ProtocolError, parse_data_line


class Vl53l0xSerialRangeNode(Node):
    def __init__(self) -> None:
        super().__init__("vl53l0x_serial_range_node")
        self.declare_parameter("device", "/dev/vl53l0x-gripper")
        self.declare_parameter("baudrate", 115200)
        self.declare_parameter("frame_id", "so101_tof_link")
        self.declare_parameter("range_topic", "/visual_grasp/tof_range")
        self.declare_parameter("status_topic", "/visual_grasp/tof_status")
        self.declare_parameter("min_range_m", 0.03)
        self.declare_parameter("max_range_m", 2.0)
        self.declare_parameter("field_of_view_rad", 0.4363)
        self.declare_parameter("serial_timeout_sec", 0.20)
        self.declare_parameter("reconnect_interval_sec", 2.0)
        self.declare_parameter("stale_timeout_sec", 0.50)
        self.declare_parameter("max_line_bytes", 256)

        sensor_qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=5,
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE,
        )
        self._range_pub = self.create_publisher(
            Range,
            str(self.get_parameter("range_topic").value),
            sensor_qos,
        )
        self._status_pub = self.create_publisher(
            String,
            str(self.get_parameter("status_topic").value),
            10,
        )

        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._accepted = 0
        self._rejected = 0
        self._last_sequence: int | None = None
        self._last_sensor_time_ms: int | None = None
        self._device_restarts = 0
        self._last_valid_monotonic: float | None = None
        self._state = "starting"
        self._reason = "initializing"
        self._thread = threading.Thread(
            target=self._serial_loop,
            name="vl53l0x-serial-reader",
            daemon=True,
        )
        self._thread.start()
        self._status_timer = self.create_timer(0.5, self._status_tick)

    def _set_status(self, state: str, reason: str) -> None:
        with self._lock:
            self._state = state
            self._reason = reason

    def _status_tick(self) -> None:
        stale_timeout = float(self.get_parameter("stale_timeout_sec").value)
        with self._lock:
            state = self._state
            reason = self._reason
            last_valid = self._last_valid_monotonic
            accepted = self._accepted
            rejected = self._rejected
            last_sequence = self._last_sequence
            last_sensor_time_ms = self._last_sensor_time_ms
            device_restarts = self._device_restarts

        age_sec = None if last_valid is None else max(0.0, time.monotonic() - last_valid)
        if state == "reading" and (age_sec is None or age_sec > stale_timeout):
            state = "fault"
            reason = "stale"

        message = String()
        message.data = json.dumps(
            {
                "state": state,
                "reason": reason,
                "accepted": accepted,
                "rejected": rejected,
                "last_sequence": last_sequence,
                "last_sensor_time_ms": last_sensor_time_ms,
                "device_restarts": device_restarts,
                "last_valid_age_sec": age_sec,
            },
            separators=(",", ":"),
        )
        self._status_pub.publish(message)

    def _handle_line(self, line: str) -> None:
        try:
            frame = parse_data_line(line)
        except ProtocolError as exc:
            with self._lock:
                self._rejected += 1
            self._set_status("reading", str(exc))
            return

        if frame is None:
            return

        with self._lock:
            if self._last_sequence is not None and frame.sequence < self._last_sequence:
                self._device_restarts += 1
            self._last_sequence = frame.sequence
            self._last_sensor_time_ms = frame.sensor_time_ms

        if frame.range_status != 0:
            with self._lock:
                self._rejected += 1
            self._set_status("reading", f"sensor_status_{frame.range_status}")
            return

        min_range = float(self.get_parameter("min_range_m").value)
        max_range = float(self.get_parameter("max_range_m").value)
        distance_m = frame.distance_mm / 1000.0
        if not min_range <= distance_m <= max_range:
            with self._lock:
                self._rejected += 1
            self._set_status("reading", "outside_configured_range")
            return

        message = Range()
        message.header.stamp = self.get_clock().now().to_msg()
        message.header.frame_id = str(self.get_parameter("frame_id").value).lstrip("/")
        message.radiation_type = Range.INFRARED
        message.field_of_view = float(self.get_parameter("field_of_view_rad").value)
        message.min_range = min_range
        message.max_range = max_range
        message.range = distance_m
        self._range_pub.publish(message)

        with self._lock:
            self._accepted += 1
            self._last_valid_monotonic = time.monotonic()
        self._set_status("reading", "valid")

    def _serial_loop(self) -> None:
        try:
            import serial
        except ImportError as exc:
            self.get_logger().error(f"pyserial missing: {exc}")
            self._set_status("fault", "pyserial_missing")
            return

        device = str(self.get_parameter("device").value)
        baudrate = int(self.get_parameter("baudrate").value)
        timeout = float(self.get_parameter("serial_timeout_sec").value)
        reconnect = float(self.get_parameter("reconnect_interval_sec").value)
        max_line = int(self.get_parameter("max_line_bytes").value)

        while rclpy.ok() and not self._stop.is_set():
            try:
                with serial.Serial(
                    device,
                    baudrate,
                    timeout=timeout,
                    xonxoff=False,
                    rtscts=False,
                    dsrdtr=False,
                ) as port:
                    port.dtr = False
                    port.rts = False
                    self.get_logger().info(
                        f"Reading VL53L0X bridge from {device} at {baudrate} 8N1"
                    )
                    self._set_status("reading", "connected")
                    while rclpy.ok() and not self._stop.is_set():
                        raw = port.read_until(b"\n", max_line + 1)
                        if not raw:
                            continue
                        if len(raw) > max_line or not raw.endswith(b"\n"):
                            with self._lock:
                                self._rejected += 1
                            self._set_status("reading", "line_too_long")
                            port.reset_input_buffer()
                            continue
                        self._handle_line(raw.decode("utf-8", errors="replace").strip())
            except Exception as exc:
                self.get_logger().error(f"VL53L0X serial fault: {exc}")
                self._set_status("fault", "serial_disconnected")
                self._stop.wait(reconnect)

    def destroy_node(self):
        self._stop.set()
        self._thread.join(timeout=2.0)
        return super().destroy_node()


def main(args=None) -> None:
    rclpy.init(args=args)
    node = Vl53l0xSerialRangeNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()
