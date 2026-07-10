#!/usr/bin/env python3
"""Drive directly from an RViz A point to a B point.

This is intentionally not Nav2. It subscribes to /clicked_point, treats the first
click as an A/start sanity check and the second click as the B target, then
publishes a simple differential-drive /cmd_vel command toward B.
"""

import argparse
import math
import sys
from typing import Optional, Tuple

import rclpy
from geometry_msgs.msg import PointStamped, Twist
from rclpy.duration import Duration
from rclpy.node import Node
from tf2_ros import Buffer, TransformException, TransformListener


def clamp(value: float, limit: float) -> float:
    return max(-limit, min(limit, value))


def normalize_angle(value: float) -> float:
    while value > math.pi:
        value -= 2.0 * math.pi
    while value < -math.pi:
        value += 2.0 * math.pi
    return value


def yaw_from_quaternion(x: float, y: float, z: float, w: float) -> float:
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


class RvizABDrive(Node):
    def __init__(self, args: argparse.Namespace) -> None:
        super().__init__("rviz_ab_drive")
        self.args = args
        self.tf_buffer = Buffer(cache_time=Duration(seconds=10.0))
        self.tf_listener = TransformListener(self.tf_buffer, self)
        self.cmd_pub = self.create_publisher(Twist, args.cmd_vel_topic, 10)
        self.point_sub = self.create_subscription(
            PointStamped, args.clicked_point_topic, self.on_clicked_point, 10
        )
        self.timer = self.create_timer(1.0 / args.rate, self.control_loop)
        self.start_point: Optional[Tuple[float, float]] = None
        self.goal_point: Optional[Tuple[float, float]] = None
        self.active = False
        self.last_status_time = self.get_clock().now()

        mode = "MOTION ENABLED" if args.enable_motion else "DRY RUN"
        self.get_logger().warn(
            f"{mode}: click A then B in RViz Publish Point. "
            "This node does no planning and no obstacle avoidance."
        )

    def on_clicked_point(self, msg: PointStamped) -> None:
        point = self.point_in_target_frame(msg)
        if point is None:
            return

        if self.start_point is None or (self.start_point and self.goal_point):
            self.stop()
            self.start_point = point
            self.goal_point = None
            self.active = False
            self.get_logger().info(
                f"A point set at x={point[0]:.3f}, y={point[1]:.3f}. "
                "Click B to start direct drive."
            )
            return

        self.goal_point = point
        if not self.start_is_near_robot():
            self.goal_point = None
            self.active = False
            self.stop()
            self.get_logger().error(
                "Robot is not near clicked A point. Move/relocate the robot near A "
                "or increase --start-tolerance if this is intentional."
            )
            return

        self.active = True
        self.get_logger().warn(
            f"B point set at x={point[0]:.3f}, y={point[1]:.3f}. "
            "Direct drive is now active."
        )

    def point_in_target_frame(self, msg: PointStamped) -> Optional[Tuple[float, float]]:
        source_frame = msg.header.frame_id.lstrip("/")
        target_frame = self.args.target_frame.lstrip("/")
        if source_frame == target_frame or not source_frame:
            return (msg.point.x, msg.point.y)

        try:
            transform = self.tf_buffer.lookup_transform(
                target_frame, source_frame, rclpy.time.Time()
            )
        except TransformException as exc:
            self.get_logger().error(
                f"Cannot transform clicked point from {source_frame} to {target_frame}: {exc}"
            )
            return None

        tx = transform.transform.translation.x
        ty = transform.transform.translation.y
        q = transform.transform.rotation
        yaw = yaw_from_quaternion(q.x, q.y, q.z, q.w)
        cos_yaw = math.cos(yaw)
        sin_yaw = math.sin(yaw)
        x = tx + cos_yaw * msg.point.x - sin_yaw * msg.point.y
        y = ty + sin_yaw * msg.point.x + cos_yaw * msg.point.y
        return (x, y)

    def robot_pose(self) -> Optional[Tuple[float, float, float]]:
        try:
            transform = self.tf_buffer.lookup_transform(
                self.args.target_frame, self.args.base_frame, rclpy.time.Time()
            )
        except TransformException as exc:
            self.get_logger().warn(f"Waiting for TF {self.args.target_frame}->{self.args.base_frame}: {exc}")
            return None

        translation = transform.transform.translation
        rotation = transform.transform.rotation
        yaw = yaw_from_quaternion(rotation.x, rotation.y, rotation.z, rotation.w)
        return (translation.x, translation.y, yaw)

    def start_is_near_robot(self) -> bool:
        if not self.args.require_start_near:
            return True
        pose = self.robot_pose()
        if pose is None or self.start_point is None:
            return False
        dx = self.start_point[0] - pose[0]
        dy = self.start_point[1] - pose[1]
        distance = math.hypot(dx, dy)
        self.get_logger().info(f"Distance from robot to A: {distance:.3f} m")
        return distance <= self.args.start_tolerance

    def control_loop(self) -> None:
        if not self.active or self.goal_point is None:
            return

        pose = self.robot_pose()
        if pose is None:
            self.stop()
            return

        robot_x, robot_y, robot_yaw = pose
        dx = self.goal_point[0] - robot_x
        dy = self.goal_point[1] - robot_y
        distance = math.hypot(dx, dy)
        target_heading = math.atan2(dy, dx)
        heading_error = normalize_angle(target_heading - robot_yaw)

        if distance <= self.args.goal_tolerance:
            self.stop()
            self.active = False
            self.get_logger().warn(f"Reached B within {distance:.3f} m. Stopped.")
            return

        msg = Twist()
        msg.angular.z = clamp(self.args.k_angular * heading_error, self.args.max_angular)
        if abs(heading_error) < self.args.stop_linear_angle:
            angle_scale = max(0.0, math.cos(heading_error))
            msg.linear.x = min(self.args.max_linear, self.args.k_linear * distance) * angle_scale
        else:
            msg.linear.x = 0.0

        if self.args.enable_motion:
            self.cmd_pub.publish(msg)

        now = self.get_clock().now()
        if (now - self.last_status_time).nanoseconds > 1_000_000_000:
            self.last_status_time = now
            prefix = "cmd" if self.args.enable_motion else "dry-run"
            self.get_logger().info(
                f"{prefix}: dist={distance:.3f} heading_error={heading_error:.3f} "
                f"vx={msg.linear.x:.3f} wz={msg.angular.z:.3f}"
            )

    def stop(self) -> None:
        if not self.args.enable_motion:
            return
        msg = Twist()
        for _ in range(3):
            self.cmd_pub.publish(msg)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enable-motion", action="store_true", help="Actually publish /cmd_vel.")
    parser.add_argument("--clicked-point-topic", default="/clicked_point")
    parser.add_argument("--cmd-vel-topic", default="/cmd_vel")
    parser.add_argument("--target-frame", default="map")
    parser.add_argument("--base-frame", default="base_footprint")
    parser.add_argument("--rate", type=float, default=20.0)
    parser.add_argument("--start-tolerance", type=float, default=0.35)
    parser.add_argument("--goal-tolerance", type=float, default=0.08)
    parser.add_argument("--max-linear", type=float, default=0.08)
    parser.add_argument("--max-angular", type=float, default=0.35)
    parser.add_argument("--k-linear", type=float, default=0.45)
    parser.add_argument("--k-angular", type=float, default=1.2)
    parser.add_argument("--stop-linear-angle", type=float, default=0.8)
    parser.add_argument(
        "--no-require-start-near",
        dest="require_start_near",
        action="store_false",
        help="Allow B click even if the robot is not near clicked A.",
    )
    parser.set_defaults(require_start_near=True)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    rclpy.init()
    node = RvizABDrive(args)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.stop()
        node.destroy_node()
        rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
