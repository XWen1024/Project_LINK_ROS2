"""ROS 2 bridge kept separate from Qt widgets and all robot rendering."""

from __future__ import annotations

import math
import queue
import threading
import time
from typing import Any

from PySide6.QtCore import QObject, Signal

from .models import GridLayer, Pose2D, laser_points, transform_points


def _yaw_from_quaternion(quaternion) -> float:
    return math.atan2(
        2.0 * (quaternion.w * quaternion.z + quaternion.x * quaternion.y),
        1.0 - 2.0 * (quaternion.y * quaternion.y + quaternion.z * quaternion.z),
    )


class RosBridge(QObject):
    system_state = Signal(dict)
    console_event = Signal(dict)
    grid_updated = Signal(str, object)
    scan_updated = Signal(object)
    cloud_updated = Signal(object)
    path_updated = Signal(object)
    robot_updated = Signal(object)
    connection_changed = Signal(bool, str)
    operation_event = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._commands: queue.Queue[tuple[Any, ...]] = queue.Queue(maxsize=200)
        self._sequence = 0
        self._node = None
        self._executor = None
        self._thread: threading.Thread | None = None
        self._rclpy = None
        self._teleop_type = None
        self._manage_type = None
        self._navigate_type = None
        self._last_state_monotonic = 0.0
        self._connected = False
        self._cloud_enabled = False

    def start(self) -> None:
        import rclpy
        from nav2_msgs.action import NavigateToPose
        from nav_msgs.msg import OccupancyGrid, Path
        from project_link_console_interfaces.action import ManageStack
        from project_link_console_interfaces.msg import ConsoleEvent, SystemState, TeleopCommand
        from rclpy.action import ActionClient
        from rclpy.executors import MultiThreadedExecutor
        from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
        from rclpy.time import Time
        from sensor_msgs.msg import LaserScan, PointCloud2
        from sensor_msgs_py import point_cloud2
        from std_srvs.srv import Trigger
        from tf2_ros import Buffer, TransformException, TransformListener

        self._rclpy = rclpy
        self._teleop_type = TeleopCommand
        self._manage_type = ManageStack
        self._navigate_type = NavigateToPose
        self._trigger_type = Trigger
        self._time_type = Time
        self._point_cloud2 = point_cloud2
        if not rclpy.ok():
            rclpy.init(args=None)
        self._node = rclpy.create_node("project_link_console_gui")
        self._teleop_pub = self._node.create_publisher(
            TeleopCommand, "/project_link/console/teleop", 20
        )
        self._manage_client = ActionClient(
            self._node, ManageStack, "/project_link/console/manage_stack"
        )
        self._navigate_client = ActionClient(self._node, NavigateToPose, "/navigate_to_pose")
        self._emergency_client = self._node.create_client(
            Trigger, "/project_link/console/emergency_stop"
        )
        self._tf_buffer = Buffer()
        self._tf_listener = TransformListener(self._tf_buffer, self._node)
        self._transform_exception = TransformException

        self._node.create_subscription(
            SystemState, "/project_link/console/system_state", self._on_system_state, 10
        )
        self._node.create_subscription(
            ConsoleEvent, "/project_link/console/events", self._on_console_event, 20
        )
        map_qos = QoSProfile(depth=1)
        map_qos.reliability = ReliabilityPolicy.RELIABLE
        map_qos.durability = DurabilityPolicy.TRANSIENT_LOCAL
        self._node.create_subscription(
            OccupancyGrid,
            "/map",
            lambda message: self._on_grid("occupancy_map", message),
            map_qos,
        )
        self._node.create_subscription(
            OccupancyGrid,
            "/global_costmap/costmap",
            lambda message: self._on_grid("global_costmap", message),
            5,
        )
        self._node.create_subscription(
            OccupancyGrid,
            "/local_costmap/costmap",
            lambda message: self._on_grid("local_costmap", message),
            5,
        )
        self._node.create_subscription(LaserScan, "/scan", self._on_scan, qos_profile_sensor_data)
        self._node.create_subscription(
            PointCloud2,
            "/point_lio/cloud_registered",
            self._on_cloud,
            qos_profile_sensor_data,
        )
        self._node.create_subscription(Path, "/plan", self._on_path, 5)
        self._node.create_timer(0.05, self._process_commands)
        self._node.create_timer(0.20, self._publish_robot_pose)
        self._node.create_timer(0.50, self._check_state_freshness)

        self._executor = MultiThreadedExecutor(num_threads=3)
        self._executor.add_node(self._node)
        self._thread = threading.Thread(target=self._executor.spin, name="console-ros", daemon=True)
        self._thread.start()
        self.connection_changed.emit(False, "等待 Orin console agent")

    def _put(self, command: tuple[Any, ...]) -> None:
        try:
            self._commands.put_nowait(command)
        except queue.Full:
            self.operation_event.emit("命令队列已满；已丢弃本次操作")

    def manage_stack(self, operation: int, restart: bool = False) -> None:
        self._put(("manage", int(operation), bool(restart)))

    def send_teleop(self, enabled: bool, deadman: bool, linear: float, angular: float) -> None:
        self._put(("teleop", bool(enabled), bool(deadman), float(linear), float(angular)))

    def send_navigation_goal(self, pose: Pose2D) -> None:
        self._put(("goal", pose))

    def emergency_stop(self) -> None:
        while True:
            try:
                self._commands.get_nowait()
            except queue.Empty:
                break
        self._put(("emergency",))

    def set_cloud_enabled(self, enabled: bool) -> None:
        self._cloud_enabled = bool(enabled)

    def _process_commands(self) -> None:
        latest_teleop = None
        while True:
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                break
            if command[0] == "teleop":
                latest_teleop = command
            elif command[0] == "manage":
                self._send_manage_goal(command[1], command[2])
            elif command[0] == "goal":
                self._send_navigation_action(command[1])
            elif command[0] == "emergency":
                self._call_emergency_stop()
        if latest_teleop is not None:
            self._publish_teleop(*latest_teleop[1:])

    def _publish_teleop(self, enabled: bool, deadman: bool, linear: float, angular: float) -> None:
        message = self._teleop_type()
        self._sequence += 1
        message.sequence = self._sequence
        message.enabled = enabled
        message.deadman = deadman
        message.linear_x = linear
        message.angular_z = angular
        self._teleop_pub.publish(message)

    def _send_manage_goal(self, operation: int, restart: bool) -> None:
        if not self._manage_client.wait_for_server(timeout_sec=0.0):
            self.operation_event.emit("Orin console agent 尚未连接")
            return
        goal = self._manage_type.Goal()
        goal.operation = operation
        goal.restart = restart
        future = self._manage_client.send_goal_async(goal, feedback_callback=self._manage_feedback)
        future.add_done_callback(self._manage_goal_response)

    def _manage_feedback(self, feedback_message) -> None:
        feedback = feedback_message.feedback
        self.operation_event.emit(f"{feedback.step}: {feedback.message}")

    def _manage_goal_response(self, future) -> None:
        try:
            handle = future.result()
        except Exception as exc:
            self.operation_event.emit(f"模式切换失败：{exc}")
            return
        if not handle.accepted:
            self.operation_event.emit("模式切换请求被拒绝")
            return
        result_future = handle.get_result_async()
        result_future.add_done_callback(
            lambda done: self.operation_event.emit(done.result().result.message)
        )

    def _send_navigation_action(self, pose: Pose2D) -> None:
        if not self._navigate_client.wait_for_server(timeout_sec=0.0):
            self.operation_event.emit("NavigateToPose 尚未就绪")
            return
        goal = self._navigate_type.Goal()
        goal.pose.header.frame_id = "map"
        goal.pose.header.stamp = self._node.get_clock().now().to_msg()
        goal.pose.pose.position.x = pose.x
        goal.pose.pose.position.y = pose.y
        goal.pose.pose.orientation.z = math.sin(pose.yaw * 0.5)
        goal.pose.pose.orientation.w = math.cos(pose.yaw * 0.5)
        future = self._navigate_client.send_goal_async(goal)
        future.add_done_callback(self._navigation_goal_response)

    def _navigation_goal_response(self, future) -> None:
        try:
            handle = future.result()
        except Exception as exc:
            self.operation_event.emit(f"导航目标发送失败：{exc}")
            return
        self.operation_event.emit("导航目标已接受" if handle.accepted else "导航目标被拒绝")

    def _call_emergency_stop(self) -> None:
        if not self._emergency_client.wait_for_service(timeout_sec=0.0):
            self.operation_event.emit("紧急停车服务不可用")
            return
        future = self._emergency_client.call_async(self._trigger_type.Request())
        future.add_done_callback(
            lambda done: self.operation_event.emit(done.result().message)
        )

    def _on_system_state(self, message) -> None:
        self._last_state_monotonic = time.monotonic()
        if not self._connected:
            self._connected = True
            self.connection_changed.emit(True, "Orin 已连接")
        self.system_state.emit(
            {
                "mode": int(message.mode),
                "mode_name": message.mode_name,
                "emergency_stop_latched": bool(message.emergency_stop_latched),
                "teleop_active": bool(message.teleop_active),
                "voice_backend": message.voice_backend,
                "message": message.message,
                "subsystems": [
                    {
                        "name": item.name,
                        "display_name": item.display_name,
                        "active_state": item.active_state,
                        "sub_state": item.sub_state,
                        "ready": bool(item.ready),
                        "severity": int(item.severity),
                        "message": item.message,
                    }
                    for item in message.subsystems
                ],
            }
        )

    def _check_state_freshness(self) -> None:
        if self._connected and time.monotonic() - self._last_state_monotonic > 2.0:
            self._connected = False
            self.connection_changed.emit(False, "Orin 状态已超时")

    def _on_console_event(self, message) -> None:
        self.console_event.emit(
            {
                "severity": int(message.severity),
                "subsystem": message.subsystem,
                "phase": message.phase,
                "delta_ms": float(message.delta_ms),
                "total_ms": float(message.total_ms),
                "message": message.message,
            }
        )

    def _on_grid(self, name: str, message) -> None:
        try:
            grid = GridLayer(
                width=int(message.info.width),
                height=int(message.info.height),
                resolution=float(message.info.resolution),
                origin_x=float(message.info.origin.position.x),
                origin_y=float(message.info.origin.position.y),
                cells=tuple(int(value) for value in message.data),
            )
        except ValueError as exc:
            self.operation_event.emit(f"忽略无效 {name}: {exc}")
            return
        self.grid_updated.emit(name, grid)

    def _on_scan(self, message) -> None:
        try:
            transform = self._tf_buffer.lookup_transform("map", message.header.frame_id, self._time_type())
        except self._transform_exception:
            return
        local_points = laser_points(
            message.ranges,
            angle_min=float(message.angle_min),
            angle_increment=float(message.angle_increment),
            range_min=float(message.range_min),
            range_max=float(message.range_max),
            step=max(1, len(message.ranges) // 1200),
        )
        translation = transform.transform.translation
        pose = Pose2D(
            float(translation.x),
            float(translation.y),
            _yaw_from_quaternion(transform.transform.rotation),
        )
        self.scan_updated.emit(transform_points(local_points, pose))

    def _on_path(self, message) -> None:
        self.path_updated.emit(
            [(float(pose.pose.position.x), float(pose.pose.position.y)) for pose in message.poses]
        )

    def _on_cloud(self, message) -> None:
        if not self._cloud_enabled:
            return
        transform = None
        if message.header.frame_id != "map":
            try:
                transform = self._tf_buffer.lookup_transform(
                    "map", message.header.frame_id, self._time_type()
                ).transform
            except self._transform_exception:
                return
        points: list[tuple[float, float]] = []
        for index, point in enumerate(
            self._point_cloud2.read_points(
                message,
                field_names=("x", "y", "z"),
                skip_nans=True,
            )
        ):
            if index % 8:
                continue
            x, y, z = float(point[0]), float(point[1]), float(point[2])
            if transform is not None:
                rotation = transform.rotation
                tx = 2.0 * (rotation.y * z - rotation.z * y)
                ty = 2.0 * (rotation.z * x - rotation.x * z)
                tz = 2.0 * (rotation.x * y - rotation.y * x)
                x, y = (
                    x + rotation.w * tx + rotation.y * tz - rotation.z * ty + transform.translation.x,
                    y + rotation.w * ty + rotation.z * tx - rotation.x * tz + transform.translation.y,
                )
            points.append((x, y))
            if len(points) >= 4000:
                break
        self.cloud_updated.emit(points)

    def _publish_robot_pose(self) -> None:
        try:
            transform = self._tf_buffer.lookup_transform("map", "base_footprint", self._time_type())
        except self._transform_exception:
            return
        translation = transform.transform.translation
        self.robot_updated.emit(
            Pose2D(
                float(translation.x),
                float(translation.y),
                _yaw_from_quaternion(transform.transform.rotation),
            )
        )

    def stop(self) -> None:
        if self._node is None:
            return
        try:
            self._publish_teleop(False, False, 0.0, 0.0)
        except Exception:
            pass
        if self._executor is not None:
            self._executor.shutdown(timeout_sec=1.0)
        if self._thread is not None:
            self._thread.join(timeout=1.5)
        self._node.destroy_node()
        self._node = None
        if self._rclpy is not None and self._rclpy.ok():
            self._rclpy.shutdown()
