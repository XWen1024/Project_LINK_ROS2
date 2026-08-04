#!/usr/bin/env python3
"""Read BU04 USB serial frames and publish validated UWB observations."""

from __future__ import annotations

import json
import threading
import time

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from project_link_uwb_interfaces.msg import UwbObservation

from .framing import JsFrameDecoder
from .protocol import PayloadRejected, ProtocolConfig, TagClockGuard, parse_payload


class UwbSerialNode(Node):
    def __init__(self) -> None:
        super().__init__("uwb_serial_node")
        self.declare_parameter("device", "")
        self.declare_parameter("baudrate", 115200)
        self.declare_parameter("tag_address", "")
        self.declare_parameter("source_id", "tag-1")
        self.declare_parameter("sensor_frame", "uwb_sensor")
        self.declare_parameter("observation_topic", "/uwb/person_observation")
        self.declare_parameter("status_topic", "/uwb/status")
        self.declare_parameter("max_payload_bytes", 4096)
        self.declare_parameter("max_coordinate_m", 30.0)
        self.declare_parameter("max_range_m", 30.0)
        self.declare_parameter("max_range_residual_m", 0.50)
        self.declare_parameter("serial_timeout_sec", 0.10)

        self._observation_pub = self.create_publisher(
            UwbObservation,
            str(self.get_parameter("observation_topic").value),
            20,
        )
        self._status_pub = self.create_publisher(String, str(self.get_parameter("status_topic").value), 10)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._accepted = 0
        self._rejected = 0
        self._status_state = "starting"
        self._status_reason = "startup"
        self.create_timer(1.0, self._periodic_status)

        device = str(self.get_parameter("device").value).strip()
        tag_address = str(self.get_parameter("tag_address").value).strip()
        if not device or not tag_address:
            self.get_logger().warn(
                "UWB serial is idle: set exact device and private tag_address at launch. "
                "No module configuration or robot command will be sent."
            )
            self._publish_status("idle", "device_or_tag_not_configured")
            return
        self._thread = threading.Thread(target=self._read_loop, name="uwb-serial-reader", daemon=True)
        self._thread.start()

    def _read_loop(self) -> None:
        try:
            import serial
        except ImportError as exc:
            self.get_logger().error(f"pyserial is required: {exc}")
            self._publish_status("fault", "pyserial_missing")
            return

        device = str(self.get_parameter("device").value).strip()
        baudrate = int(self.get_parameter("baudrate").value)
        timeout = float(self.get_parameter("serial_timeout_sec").value)
        decoder = JsFrameDecoder(int(self.get_parameter("max_payload_bytes").value))
        config = ProtocolConfig(
            tag_address=str(self.get_parameter("tag_address").value),
            source_id=str(self.get_parameter("source_id").value),
            max_coordinate_m=float(self.get_parameter("max_coordinate_m").value),
            max_range_m=float(self.get_parameter("max_range_m").value),
            max_range_residual_m=float(self.get_parameter("max_range_residual_m").value),
        )
        clock_guard = TagClockGuard()
        try:
            with serial.Serial(
                device,
                baudrate,
                timeout=timeout,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False,
            ) as port:
                self.get_logger().info(f"Reading BU04 frames from {device} at {baudrate} 8N1.")
                self._publish_status("reading", "connected")
                while rclpy.ok() and not self._stop.is_set():
                    chunk = port.read(1024)
                    if not chunk:
                        continue
                    receive_time_ns = time.monotonic_ns()
                    for payload in decoder.feed(chunk):
                        try:
                            sample = parse_payload(payload, receive_time_ns, config)
                            if not clock_guard.accept(sample.tag_time_raw):
                                raise PayloadRejected("tag_time_not_increasing")
                        except PayloadRejected as exc:
                            self._rejected += 1
                            self._publish_status("reading", exc.reason)
                            continue
                        self._accepted += 1
                        message = UwbObservation()
                        message.header.stamp = self.get_clock().now().to_msg()
                        message.header.frame_id = str(self.get_parameter("sensor_frame").value).lstrip("/")
                        message.source_id = sample.source_id
                        message.tag_time_raw = sample.tag_time_raw
                        message.x_m = sample.x_m
                        message.y_m = sample.y_m
                        message.range_m = sample.range_m
                        message.coordinate_range_m = sample.coordinate_range_m
                        message.range_residual_m = sample.range_residual_m
                        message.valid = True
                        self._observation_pub.publish(message)
        except Exception as exc:
            self.get_logger().error(f"UWB serial stopped fail-closed: {exc}")
            self._publish_status("fault", "serial_disconnected")

    def _publish_status(self, state: str, reason: str) -> None:
        self._status_state = state
        self._status_reason = reason
        message = String()
        message.data = json.dumps(
            {
                "state": state,
                "reason": reason,
                "accepted": self._accepted,
                "rejected": self._rejected,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        self._status_pub.publish(message)

    def _periodic_status(self) -> None:
        self._publish_status(self._status_state, self._status_reason)

    def destroy_node(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        return super().destroy_node()


def main() -> None:
    rclpy.init()
    node = UwbSerialNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
