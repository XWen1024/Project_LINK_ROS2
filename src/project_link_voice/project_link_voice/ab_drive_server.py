#!/usr/bin/env python3
"""Guarded direct A-to-B action server. This is deliberately not Nav2."""

from __future__ import annotations

import math
import threading
import time
from typing import Optional

import rclpy
from geometry_msgs.msg import PoseStamped, Twist
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.duration import Duration
from rclpy.node import Node
from tf2_ros import Buffer, TransformException, TransformListener

from project_link_voice_interfaces.action import DriveToPoint


STATUS_SUCCEEDED = 0
STATUS_CANCELED = 1
STATUS_REJECTED = 2
STATUS_TIMEOUT = 3
STATUS_TF_ERROR = 4


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


class AbDriveServer(Node):
    """The sole voice-controlled publisher for the direct-drive `/cmd_vel` path."""

    def __init__(self) -> None:
        super().__init__("ab_drive_server")
        self.declare_parameter("enable_motion", False)
        self.declare_parameter("cmd_vel_topic", "/cmd_vel")
        self.declare_parameter("target_frame", "map")
        self.declare_parameter("base_frame", "base_footprint")
        self.declare_parameter("control_rate_hz", 20.0)
        self.declare_parameter("goal_tolerance_m", 0.08)
        self.declare_parameter("max_goal_distance_m", 3.0)
        self.declare_parameter("max_motion_sec", 30.0)
        self.declare_parameter("max_linear_mps", 0.08)
        self.declare_parameter("max_angular_rps", 0.35)
        self.declare_parameter("linear_gain", 0.45)
        self.declare_parameter("angular_gain", 1.2)
        self.declare_parameter("stop_linear_angle_rad", 0.8)
        self.declare_parameter("command_watchdog_sec", 0.5)

        self._target_frame = str(self.get_parameter("target_frame").value).lstrip("/")
        self._base_frame = str(self.get_parameter("base_frame").value).lstrip("/")
        self._control_rate = float(self.get_parameter("control_rate_hz").value)
        self._cmd_pub = self.create_publisher(Twist, str(self.get_parameter("cmd_vel_topic").value), 10)
        self._tf_buffer = Buffer(cache_time=Duration(seconds=10.0))
        self._tf_listener = TransformListener(self._tf_buffer, self)
        self._active_lock = threading.Lock()
        self._active = False
        self._reserved = False
        self._last_command_monotonic = 0.0
        self._watchdog = self.create_timer(0.1, self._watchdog_callback)
        self._server = ActionServer(
            self,
            DriveToPoint,
            "/voice/drive_to_point",
            execute_callback=self.execute_callback,
            goal_callback=self.goal_callback,
            cancel_callback=self.cancel_callback,
        )
        self.get_logger().warn("Direct drive has no planning or obstacle avoidance. Keep the physical E-stop available.")

    def goal_callback(self, goal_request: DriveToPoint.Goal) -> GoalResponse:
        if not bool(self.get_parameter("enable_motion").value):
            self.get_logger().error("Rejected direct-drive goal because enable_motion is false.")
            return GoalResponse.REJECT
        if goal_request.target.header.frame_id.lstrip("/") != self._target_frame:
            self.get_logger().error("Rejected goal outside the map target frame.")
            return GoalResponse.REJECT
        with self._active_lock:
            if self._active or self._reserved:
                self.get_logger().error("Rejected goal because another direct-drive goal is active.")
                return GoalResponse.REJECT
            self._reserved = True
        pose = self.robot_pose()
        if pose is None:
            self._release_reservation()
            self.get_logger().error("Rejected goal because map->base TF is unavailable.")
            return GoalResponse.REJECT
        distance = math.hypot(goal_request.target.pose.position.x - pose[0], goal_request.target.pose.position.y - pose[1])
        if distance > float(self.get_parameter("max_goal_distance_m").value):
            self._release_reservation()
            self.get_logger().error(f"Rejected goal at {distance:.2f} m: exceeds maximum direct-drive distance.")
            return GoalResponse.REJECT
        return GoalResponse.ACCEPT

    def _release_reservation(self) -> None:
        with self._active_lock:
            self._reserved = False

    def cancel_callback(self, _goal_handle) -> CancelResponse:
        self.stop()
        return CancelResponse.ACCEPT

    def robot_pose(self) -> Optional[tuple[float, float, float]]:
        try:
            transform = self._tf_buffer.lookup_transform(self._target_frame, self._base_frame, rclpy.time.Time())
        except TransformException as exc:
            self.get_logger().warn(f"Waiting for TF {self._target_frame}->{self._base_frame}: {exc}")
            return None
        translation = transform.transform.translation
        rotation = transform.transform.rotation
        return translation.x, translation.y, yaw_from_quaternion(rotation.x, rotation.y, rotation.z, rotation.w)

    def publish_command(self, linear: float, angular: float) -> None:
        command = Twist()
        command.linear.x = linear
        command.angular.z = angular
        self._cmd_pub.publish(command)
        self._last_command_monotonic = time.monotonic()

    def stop(self) -> None:
        command = Twist()
        for _ in range(3):
            self._cmd_pub.publish(command)
        self._last_command_monotonic = time.monotonic()

    def _watchdog_callback(self) -> None:
        with self._active_lock:
            active = self._active
        timeout = float(self.get_parameter("command_watchdog_sec").value)
        if active and time.monotonic() - self._last_command_monotonic > timeout:
            self.get_logger().error("Command watchdog expired; stopping direct drive.")
            self.stop()

    def _result(self, status: int, message: str) -> DriveToPoint.Result:
        result = DriveToPoint.Result()
        result.status = status
        result.message = message
        return result

    def execute_callback(self, goal_handle):
        with self._active_lock:
            self._active = True
        start = time.monotonic()
        target = goal_handle.request.target.pose.position
        period = 1.0 / self._control_rate
        try:
            while rclpy.ok():
                if goal_handle.is_cancel_requested:
                    self.stop()
                    goal_handle.canceled()
                    return self._result(STATUS_CANCELED, "Direct-drive goal canceled and zero velocity published.")
                if time.monotonic() - start >= float(self.get_parameter("max_motion_sec").value):
                    self.stop()
                    goal_handle.abort()
                    return self._result(STATUS_TIMEOUT, "Maximum direct-drive duration reached; stopped.")
                pose = self.robot_pose()
                if pose is None:
                    self.stop()
                    goal_handle.abort()
                    return self._result(STATUS_TF_ERROR, "Lost map-to-base TF; stopped.")
                robot_x, robot_y, robot_yaw = pose
                distance = math.hypot(target.x - robot_x, target.y - robot_y)
                if distance <= float(self.get_parameter("goal_tolerance_m").value):
                    self.stop()
                    goal_handle.succeed()
                    return self._result(STATUS_SUCCEEDED, f"Reached target within {distance:.3f} m.")
                target_heading = math.atan2(target.y - robot_y, target.x - robot_x)
                heading_error = normalize_angle(target_heading - robot_yaw)
                angular = clamp(
                    float(self.get_parameter("angular_gain").value) * heading_error,
                    float(self.get_parameter("max_angular_rps").value),
                )
                if abs(heading_error) < float(self.get_parameter("stop_linear_angle_rad").value):
                    linear = min(
                        float(self.get_parameter("max_linear_mps").value),
                        float(self.get_parameter("linear_gain").value) * distance,
                    ) * max(0.0, math.cos(heading_error))
                else:
                    linear = 0.0
                self.publish_command(linear, angular)
                feedback = DriveToPoint.Feedback()
                feedback.state = "driving"
                feedback.distance_remaining = distance
                feedback.linear_velocity = linear
                feedback.angular_velocity = angular
                goal_handle.publish_feedback(feedback)
                time.sleep(period)
        except Exception as exc:
            self.get_logger().error(f"Direct-drive action failed: {exc}")
            self.stop()
            goal_handle.abort()
            return self._result(STATUS_TF_ERROR, f"Direct-drive failure: {exc}")
        finally:
            self.stop()
            with self._active_lock:
                self._active = False
                self._reserved = False

    def destroy_node(self):
        self.stop()
        self._server.destroy()
        return super().destroy_node()


def main() -> None:
    rclpy.init()
    node = AbDriveServer()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()