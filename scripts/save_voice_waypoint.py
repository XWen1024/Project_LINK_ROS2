#!/usr/bin/env python3
"""Save voice waypoints from current TF or RViz /clicked_point."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import rclpy
from geometry_msgs.msg import PointStamped
from rclpy.node import Node
from tf2_ros import Buffer, TransformException, TransformListener


def yaw_from_quaternion(x: float, y: float, z: float, w: float) -> float:
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def load_waypoints(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"Waypoint file must contain a JSON object: {path}")
    return value


def save_waypoint(path: Path, name: str, x: float, y: float, yaw: float) -> None:
    data = load_waypoints(path)
    data[name] = {"x": round(float(x), 4), "y": round(float(y), 4), "yaw": round(float(yaw), 4)}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


class WaypointCapture(Node):
    def __init__(self, args: argparse.Namespace) -> None:
        super().__init__("voice_waypoint_capture")
        self.args = args
        self.output = Path(args.output).expanduser()
        self.names = list(args.names or [])
        self.next_index = 0
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)
        self.subscription = None

    def current_pose(self) -> tuple[float, float, float]:
        transform = self.tf_buffer.lookup_transform(self.args.frame, self.args.base_frame, rclpy.time.Time())
        translation = transform.transform.translation
        rotation = transform.transform.rotation
        yaw = yaw_from_quaternion(rotation.x, rotation.y, rotation.z, rotation.w)
        return translation.x, translation.y, yaw

    def current_yaw_or_default(self) -> float:
        if self.args.yaw is not None:
            return float(self.args.yaw)
        try:
            return self.current_pose()[2]
        except TransformException:
            return 0.0

    def save_current_pose(self, name: str) -> None:
        x, y, yaw = self.current_pose()
        save_waypoint(self.output, name, x, y, yaw)
        print(f"Saved '{name}' from TF {self.args.frame}->{self.args.base_frame}: x={x:.3f}, y={y:.3f}, yaw={yaw:.3f}")

    def start_click_capture(self) -> None:
        if not self.names:
            raise ValueError("--names is required for --from-click")
        print(f"Saving RViz clicks to: {self.output}")
        print("In RViz, use Publish Point on /clicked_point.")
        print("Click these points in order:")
        for index, name in enumerate(self.names, 1):
            print(f"  {index}. {name}")
        self.subscription = self.create_subscription(PointStamped, self.args.topic, self.on_click, 10)

    def on_click(self, message: PointStamped) -> None:
        if self.next_index >= len(self.names):
            return
        if message.header.frame_id and message.header.frame_id.lstrip("/") != self.args.frame:
            self.get_logger().warn(
                f"Clicked point frame is {message.header.frame_id}; expected {self.args.frame}. Saving numeric values anyway."
            )
        name = self.names[self.next_index]
        yaw = self.current_yaw_or_default()
        save_waypoint(self.output, name, message.point.x, message.point.y, yaw)
        print(f"Saved '{name}' from click: x={message.point.x:.3f}, y={message.point.y:.3f}, yaw={yaw:.3f}")
        self.next_index += 1
        if self.next_index >= len(self.names):
            print("All requested waypoints saved.")
            rclpy.shutdown()
        else:
            print(f"Next click will save: {self.names[self.next_index]}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="~/.ros/project_link_voice/waypoints.json")
    parser.add_argument("--frame", default="map")
    parser.add_argument("--base-frame", default="base_footprint")
    parser.add_argument("--topic", default="/clicked_point")
    parser.add_argument("--yaw", type=float, default=None, help="Yaw to save for clicked points. Default: current robot yaw, or 0.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--from-tf", metavar="NAME", help="Save current robot TF pose as NAME.")
    mode.add_argument("--from-click", action="store_true", help="Save RViz clicked points using --names.")
    mode.add_argument("--list", action="store_true", help="List saved waypoints.")
    parser.add_argument("--names", nargs="+", help="Waypoint names for --from-click, saved in click order.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = Path(args.output).expanduser()
    if args.list:
        for name, value in sorted(load_waypoints(output).items()):
            print(f"{name}: x={value.get('x')}, y={value.get('y')}, yaw={value.get('yaw', 0.0)}")
        return 0

    rclpy.init()
    node = WaypointCapture(args)
    try:
        if args.from_tf:
            node.save_current_pose(args.from_tf)
        else:
            node.start_click_capture()
            rclpy.spin(node)
    finally:
        if rclpy.ok():
            node.destroy_node()
            rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
