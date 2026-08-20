"""ROS 2 bridge kept separate from Qt widgets and all robot rendering."""

from __future__ import annotations

import json
import math
import os
import queue
import threading
import time
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from .models import GridLayer, Pose2D, laser_points, transform_points


def _yaw_from_quaternion(quaternion) -> float:
    return math.atan2(
        2.0 * (quaternion.w * quaternion.z + quaternion.x * quaternion.y),
        1.0 - 2.0 * (quaternion.y * quaternion.y + quaternion.z * quaternion.z),
    )


def _quaternion_from_rpy(roll: float, pitch: float, yaw: float) -> tuple[float, float, float, float]:
    cr = math.cos(roll * 0.5)
    sr = math.sin(roll * 0.5)
    cp = math.cos(pitch * 0.5)
    sp = math.sin(pitch * 0.5)
    cy = math.cos(yaw * 0.5)
    sy = math.sin(yaw * 0.5)
    return (
        sr * cp * cy - cr * sp * sy,
        cr * sp * cy + sr * cp * sy,
        cr * cp * sy - sr * sp * cy,
        cr * cp * cy + sr * sp * sy,
    )


def _multiply_quaternions(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    lx, ly, lz, lw = left
    rx, ry, rz, rw = right
    return (
        lw * rx + lx * rw + ly * rz - lz * ry,
        lw * ry - lx * rz + ly * rw + lz * rx,
        lw * rz + lx * ry - ly * rx + lz * rw,
        lw * rw - lx * rx - ly * ry - lz * rz,
    )


class RosBridge(QObject):
    system_state = Signal(dict)
    console_event = Signal(dict)
    grid_updated = Signal(str, object)
    scan_updated = Signal(object)
    cloud_updated = Signal(object)
    path_updated = Signal(object)
    robot_updated = Signal(object)
    front_camera_image = Signal(bytes)
    front_camera_parameters = Signal(dict)
    front_camera_configured = Signal(bool, str)
    connection_changed = Signal(bool, str)
    operation_event = Signal(str)
    stack_progress = Signal(dict)
    lifecycle_completed = Signal(str, bool)
    voice_status = Signal(dict)
    voice_control_available = Signal(bool, str)
    voice_operation = Signal(str)
    manipulation_control_available = Signal(bool, str)
    manipulation_operation = Signal(str)
    fall_status = Signal(dict)
    fall_events = Signal(list)
    fall_event_detail = Signal(dict)
    fall_evidence_image = Signal(bytes)
    fall_operation = Signal(str)
    fall_control_available = Signal(bool, str)
    uwb_observation = Signal(dict)
    uwb_status = Signal(str)
    uwb_goal = Signal(dict)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._commands: queue.Queue[tuple[Any, ...]] = queue.Queue(maxsize=200)
        self._teleop_lock = threading.Lock()
        self._pending_teleop: tuple[bool, bool, float, float] | None = None
        self._pending_manage: dict[str, Any] | None = None
        self._last_mode: int | None = None
        self._sequence = 0
        self._node = None
        self._executor = None
        self._thread: threading.Thread | None = None
        self._command_guard = None
        self._command_callback_group = None
        self._rclpy = None
        self._teleop_type = None
        self._manage_type = None
        self._navigate_type = None
        self._last_state_monotonic = 0.0
        self._connected = False
        self._connection_text = "等待 Orin console agent"
        self._cloud_enabled = False
        self._lidar_calibration_enabled = False
        self._cloud_subscription = None
        self._sensor_qos = None
        self._lidar_preview_rpy = (-1.5707963268, -0.0383972435, 1.5707963268)
        self._lidar_preview_frame = "project_link_lidar_calibration"
        self._front_camera_lock = threading.Lock()
        self._pending_front_camera: bytes | None = None
        self._front_camera_timer = QTimer(self)
        self._front_camera_timer.setInterval(33)
        self._front_camera_timer.timeout.connect(self._flush_front_camera)
        self._front_camera_timer.start()
        self._voice_control_ready: bool | None = None
        self._manipulation_control_ready: bool | None = None
        self._fall_control_ready: bool | None = None
        self._uwb_enabled = os.environ.get("PROJECT_LINK_SHOW_UWB_PAGE", "0") == "1"

    def start(self) -> None:
        import rclpy
        from geometry_msgs.msg import PoseStamped, TransformStamped
        from nav2_msgs.action import NavigateToPose
        from nav_msgs.msg import OccupancyGrid, Path
        from project_link_console_interfaces.action import ManageStack, SwitchVoice
        from project_link_console_interfaces.msg import ConsoleEvent, SystemState, TeleopCommand
        from project_link_emergency_interfaces.msg import FallResponseStatus
        from project_link_emergency_interfaces.srv import GetFallEvent, ListFallEvents
        from rcl_interfaces.srv import GetParameters, SetParameters
        from rclpy.action import ActionClient
        from rclpy.callback_groups import MutuallyExclusiveCallbackGroup
        from rclpy.executors import MultiThreadedExecutor
        from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
        from rclpy.parameter import Parameter
        from rclpy.signals import SignalHandlerOptions
        from rclpy.time import Time
        from sensor_msgs.msg import CompressedImage, LaserScan, PointCloud2
        from sensor_msgs_py import point_cloud2
        from std_msgs.msg import String
        from std_srvs.srv import Trigger
        from tf2_ros import Buffer, TransformBroadcaster, TransformException, TransformListener

        self._rclpy = rclpy
        self._teleop_type = TeleopCommand
        self._manage_type = ManageStack
        self._switch_voice_type = SwitchVoice
        self._navigate_type = NavigateToPose
        self._trigger_type = Trigger
        self._time_type = Time
        self._point_cloud2 = point_cloud2
        self._point_cloud_type = PointCloud2
        self._sensor_qos = qos_profile_sensor_data
        self._transform_stamped_type = TransformStamped
        self._parameter_type = Parameter
        self._get_parameters_type = GetParameters
        self._set_parameters_type = SetParameters
        self._get_fall_event_type = GetFallEvent
        self._list_fall_events_type = ListFallEvents
        if not rclpy.ok():
            rclpy.init(args=None, signal_handler_options=SignalHandlerOptions.NO)
        self._node = rclpy.create_node("project_link_console_gui")
        self._command_callback_group = MutuallyExclusiveCallbackGroup()
        self._teleop_pub = self._node.create_publisher(
            TeleopCommand, "/project_link/console/teleop", 20
        )
        self._manage_client = ActionClient(
            self._node,
            ManageStack,
            "/project_link/console/manage_stack",
            callback_group=self._command_callback_group,
        )
        self._switch_voice_client = ActionClient(
            self._node,
            SwitchVoice,
            "/project_link/console/switch_voice",
            callback_group=self._command_callback_group,
        )
        self._navigate_client = ActionClient(
            self._node,
            NavigateToPose,
            "/navigate_to_pose",
            callback_group=self._command_callback_group,
        )
        self._emergency_client = self._node.create_client(
            Trigger, "/project_link/console/emergency_stop"
        )
        self._front_camera_get_client = self._node.create_client(
            GetParameters, "/project_link_front_camera/get_parameters"
        )
        self._front_camera_set_client = self._node.create_client(
            SetParameters, "/project_link_front_camera/set_parameters"
        )
        self._start_uwb_client = None
        self._stop_uwb_client = None
        self._start_visual_grasp_client = self._node.create_client(
            Trigger, "/project_link/console/start_visual_grasp"
        )
        self._stop_visual_grasp_client = self._node.create_client(
            Trigger, "/project_link/console/stop_visual_grasp"
        )
        self._start_fall_client = self._node.create_client(
            Trigger, "/project_link/console/start_fall_response"
        )
        self._stop_fall_client = self._node.create_client(
            Trigger, "/project_link/console/stop_fall_response"
        )
        self._restart_fall_client = self._node.create_client(
            Trigger, "/project_link/console/restart_fall_response"
        )
        self._restart_wechat_client = self._node.create_client(
            Trigger, "/project_link/console/restart_wechatbot"
        )
        self._cancel_fall_client = self._node.create_client(
            Trigger, "/fall_detection/cancel_active"
        )
        self._create_fall_demo_client = self._node.create_client(
            Trigger, "/fall_detection/create_demo_event"
        )
        self._fall_preflight_client = self._node.create_client(
            Trigger, "/fall_detection/run_preflight"
        )
        self._get_fall_event_client = self._node.create_client(
            GetFallEvent, "/fall_detection/get_event"
        )
        self._list_fall_events_client = self._node.create_client(
            ListFallEvents, "/fall_detection/list_events"
        )
        if self._uwb_enabled:
            self._start_uwb_client = self._node.create_client(
                Trigger, "/project_link/console/start_uwb_shadow"
            )
            self._stop_uwb_client = self._node.create_client(
                Trigger, "/project_link/console/stop_uwb_shadow"
            )
        self._tf_buffer = Buffer()
        self._tf_listener = TransformListener(self._tf_buffer, self._node)
        self._lidar_preview_tf = TransformBroadcaster(self._node)
        self._transform_exception = TransformException
        self._lidar_preview_pub = self._node.create_publisher(
            PointCloud2,
            "/project_link/lidar_calibration/cloud",
            qos_profile_sensor_data,
        )

        self._node.create_subscription(
            SystemState, "/project_link/console/system_state", self._on_system_state, 10
        )
        self._node.create_subscription(
            ConsoleEvent, "/project_link/console/events", self._on_console_event, 20
        )
        self._node.create_subscription(String, "/voice/status", self._on_voice_status, 10)
        self._node.create_subscription(
            FallResponseStatus,
            "/fall_detection/status",
            self._on_fall_status,
            10,
        )
        if self._uwb_enabled:
            from project_link_uwb_interfaces.msg import UwbObservation

            self._node.create_subscription(
                UwbObservation, "/uwb/person_observation", self._on_uwb_observation, 20
            )
            self._node.create_subscription(String, "/uwb/status", self._on_uwb_status, 10)
            self._node.create_subscription(
                String, "/uwb_navigation/status", self._on_uwb_status, 10
            )
            self._node.create_subscription(
                PoseStamped, "/uwb_navigation/proposed_goal", self._on_uwb_goal, 10
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
            CompressedImage,
            "/front_camera/image/compressed",
            self._on_front_camera,
            qos_profile_sensor_data,
        )
        self._node.create_subscription(
            CompressedImage,
            "/fall_detection/evidence/compressed",
            lambda message: self.fall_evidence_image.emit(bytes(message.data)),
            qos_profile_sensor_data,
        )
        self._node.create_subscription(Path, "/plan", self._on_path, 5)
        self._command_guard = self._node.create_guard_condition(
            self._process_commands,
            callback_group=self._command_callback_group,
        )
        self._node.create_timer(
            0.05,
            self._process_commands,
            callback_group=self._command_callback_group,
        )
        self._node.create_timer(0.20, self._publish_robot_pose)
        self._node.create_timer(0.50, self._check_state_freshness)
        self._node.create_timer(1.00, self._check_voice_control)

        self.connection_changed.emit(False, self._connection_text)
        self.voice_control_available.emit(False, "等待发现 Orin 语音控制")
        self.manipulation_control_available.emit(False, "等待发现 Orin 机械臂控制")
        self.fall_control_available.emit(False, "等待发现 Orin 跌倒检测控制")
        self._executor = MultiThreadedExecutor(num_threads=3)
        self._executor.add_node(self._node)
        self._thread = threading.Thread(target=self._executor.spin, name="console-ros", daemon=True)
        self._thread.start()

    def connection_snapshot(self) -> tuple[bool, str]:
        return self._connected, self._connection_text

    def _put(self, command: tuple[Any, ...]) -> None:
        try:
            self._commands.put_nowait(command)
        except queue.Full:
            self.operation_event.emit("命令队列已满；已丢弃本次操作")
            return
        # Wake the ROS executor immediately. Relying only on the polling timer
        # can starve lifecycle requests behind map, camera and point-cloud work.
        guard = self._command_guard
        if guard is not None:
            guard.trigger()

    def _on_front_camera(self, message) -> None:
        with self._front_camera_lock:
            self._pending_front_camera = bytes(message.data)

    def _flush_front_camera(self) -> None:
        with self._front_camera_lock:
            jpeg_data = self._pending_front_camera
            self._pending_front_camera = None
        if jpeg_data is not None:
            self.front_camera_image.emit(jpeg_data)

    def manage_stack(self, operation: int, restart: bool = False) -> None:
        expected_modes = {
            1: {1},
            2: {2},
            3: {3},
            4: {0, 1},
            5: {0},
        }
        if not restart and self._last_mode in expected_modes.get(int(operation), set()):
            message = "Orin 已经处于目标模式，无需重复启动"
            self.operation_event.emit(message)
            self.stack_progress.emit(
                {
                    "state": "complete",
                    "step": "状态确认",
                    "progress": 1.0,
                    "message": message,
                }
            )
            self.lifecycle_completed.emit("stack", True)
            return
        self._pending_manage = {
            "operation": int(operation),
            "restart": bool(restart),
            "saw_transitioning": False,
        }
        self._put(("manage", int(operation), bool(restart)))

    def send_teleop(self, enabled: bool, deadman: bool, linear: float, angular: float) -> None:
        # Teleop is a latest-value heartbeat, not a lifecycle command. Coalesce
        # it outside the bounded command queue so a disconnected/stalled DDS
        # executor can never let heartbeats crowd out Start Navigation2.
        with self._teleop_lock:
            self._pending_teleop = (
                bool(enabled),
                bool(deadman),
                float(linear),
                float(angular),
            )
        guard = self._command_guard
        if guard is not None:
            guard.trigger()

    def send_navigation_goal(self, pose: Pose2D) -> None:
        self._put(("goal", pose))

    def emergency_stop(self) -> None:
        while True:
            try:
                self._commands.get_nowait()
            except queue.Empty:
                break
        self._put(("emergency",))

    def switch_voice(self, backend: int) -> None:
        self._put(("switch_voice", int(backend)))

    def probe_voice_control(self) -> None:
        self._put(("probe_voice",))

    def start_visual_grasp(self) -> None:
        self._put(("visual_grasp", True))

    def stop_visual_grasp(self) -> None:
        self._put(("visual_grasp", False))

    def start_fall_response(self) -> None:
        self._put(("fall_lifecycle", "start"))

    def stop_fall_response(self) -> None:
        self._put(("fall_lifecycle", "stop"))

    def restart_fall_response(self) -> None:
        self._put(("fall_lifecycle", "restart"))

    def restart_wechatbot(self) -> None:
        self._put(("fall_lifecycle", "restart_wechat"))

    def cancel_fall_response(self) -> None:
        self._put(("fall_cancel",))

    def create_fall_demo_event(self) -> None:
        self._put(("fall_demo",))

    def run_fall_preflight(self) -> None:
        self._put(("fall_preflight",))

    def request_fall_events(self, limit: int = 20) -> None:
        self._put(("fall_events", int(limit)))

    def request_fall_event(self, event_id: str) -> None:
        self._put(("fall_event", str(event_id)))

    def request_front_camera_parameters(self) -> None:
        self._put(("front_camera_get",))

    def set_front_camera_exposure(
        self,
        automatic: bool,
        exposure: int,
        gain: int,
        automatic_white_balance: bool = True,
        white_balance_temperature: int = 3400,
    ) -> None:
        self._put(
            (
                "front_camera_set",
                bool(automatic),
                int(exposure),
                int(gain),
                bool(automatic_white_balance),
                int(white_balance_temperature),
            )
        )

    def start_uwb_shadow(self) -> None:
        self._put(("uwb_shadow", True))

    def stop_uwb_shadow(self) -> None:
        self._put(("uwb_shadow", False))

    def set_cloud_enabled(self, enabled: bool) -> None:
        self._put(("cloud_enabled", bool(enabled)))

    def set_lidar_preview_rpy(self, roll: float, pitch: float, yaw: float) -> None:
        self._lidar_preview_rpy = (float(roll), float(pitch), float(yaw))

    def set_lidar_calibration_enabled(self, enabled: bool) -> None:
        self._put(("lidar_calibration_enabled", bool(enabled)))

    def _process_commands(self) -> None:
        while True:
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                break
            try:
                if command[0] == "manage":
                    self._send_manage_goal(command[1], command[2])
                elif command[0] == "goal":
                    self._send_navigation_action(command[1])
                elif command[0] == "emergency":
                    self._call_emergency_stop()
                elif command[0] == "switch_voice":
                    self._switch_voice(command[1])
                elif command[0] == "probe_voice":
                    self._probe_voice_control()
                elif command[0] == "visual_grasp":
                    self._set_visual_grasp(command[1])
                elif command[0] == "fall_lifecycle":
                    self._set_fall_lifecycle(command[1])
                elif command[0] == "fall_cancel":
                    self._call_fall_trigger(self._cancel_fall_client, "取消跌倒处置")
                elif command[0] == "fall_demo":
                    self._call_fall_trigger(self._create_fall_demo_client, "创建演示事件")
                elif command[0] == "fall_preflight":
                    self._call_fall_trigger(self._fall_preflight_client, "运行 Nav2 预检")
                elif command[0] == "fall_events":
                    self._request_fall_events(command[1])
                elif command[0] == "fall_event":
                    self._request_fall_event(command[1])
                elif command[0] == "front_camera_get":
                    self._get_front_camera_parameters()
                elif command[0] == "front_camera_set":
                    self._set_front_camera_parameters(*command[1:])
                elif command[0] == "cloud_enabled":
                    self._cloud_enabled = bool(command[1])
                    self._sync_cloud_subscription()
                elif command[0] == "lidar_calibration_enabled":
                    self._lidar_calibration_enabled = bool(command[1])
                    self._sync_cloud_subscription()
                elif command[0] == "uwb_shadow":
                    self._set_uwb_shadow(command[1])
            except Exception as exc:
                message = f"中控命令执行失败：{type(exc).__name__}: {exc}"
                self.operation_event.emit(message)
                if command[0] == "manage":
                    self._pending_manage = None
                    self.stack_progress.emit(
                        {"state": "failed", "progress": 0.0, "message": message}
                    )
                    self.lifecycle_completed.emit("stack", False)
        with self._teleop_lock:
            latest_teleop = self._pending_teleop
            self._pending_teleop = None
        if latest_teleop is not None:
            try:
                self._publish_teleop(*latest_teleop)
            except Exception as exc:
                self.operation_event.emit(f"遥控心跳发送失败：{exc}")

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
        self.stack_progress.emit(
            {
                "state": "running",
                "step": "连接中控代理",
                "progress": 0.03,
                "message": "正在确认 Orin 管理端点",
            }
        )
        if not self._manage_client.wait_for_server(timeout_sec=1.0):
            self._pending_manage = None
            self.operation_event.emit("Orin console agent 尚未连接")
            self.stack_progress.emit(
                {
                    "state": "failed",
                    "progress": 0.0,
                    "message": "Orin console agent 尚未连接",
                }
            )
            self.lifecycle_completed.emit("stack", False)
            return
        goal = self._manage_type.Goal()
        goal.operation = operation
        goal.restart = restart
        future = self._manage_client.send_goal_async(goal, feedback_callback=self._manage_feedback)
        future.add_done_callback(self._manage_goal_response)

    def _set_visual_grasp(self, enabled: bool) -> None:
        client = self._start_visual_grasp_client if enabled else self._stop_visual_grasp_client
        if not client.wait_for_service(timeout_sec=0.25):
            message = "Orin 机械臂生命周期控制尚未连接"
            self.manipulation_control_available.emit(False, message)
            self.manipulation_operation.emit(message)
            return
        self.manipulation_control_available.emit(True, "Orin 机械臂生命周期控制已连接")
        future = client.call_async(self._trigger_type.Request())
        future.add_done_callback(
            lambda done, action="manipulation": self._manipulation_done(done, action)
        )

    def _manipulation_done(self, future, action: str) -> None:
        try:
            response = future.result()
            message = response.message
            success = bool(response.success)
        except Exception as exc:
            message = f"机械臂生命周期操作失败：{exc}"
            success = False
        self.manipulation_operation.emit(message)
        self.operation_event.emit(message)
        self.lifecycle_completed.emit(action, success)

    def _set_fall_lifecycle(self, operation: str) -> None:
        clients = {
            "start": self._start_fall_client,
            "stop": self._stop_fall_client,
            "restart": self._restart_fall_client,
            "restart_wechat": self._restart_wechat_client,
        }
        client = clients.get(operation)
        if client is None or not client.wait_for_service(timeout_sec=0.25):
            message = "Orin 跌倒检测生命周期控制尚未连接"
            self.fall_control_available.emit(False, message)
            self.fall_operation.emit(message)
            return
        self.fall_control_available.emit(True, "Orin 跌倒检测控制已连接")
        client.call_async(self._trigger_type.Request()).add_done_callback(
            lambda future: self._fall_trigger_done(future, "fall")
        )

    def _call_fall_trigger(self, client, description: str) -> None:
        if not client.wait_for_service(timeout_sec=0.25):
            self.fall_operation.emit(description + "失败：服务尚未连接")
            return
        client.call_async(self._trigger_type.Request()).add_done_callback(
            lambda future: self._fall_trigger_done(future, "fall")
        )

    def _fall_trigger_done(self, future, area: str) -> None:
        try:
            response = future.result()
            success = bool(response.success)
            message = str(response.message)
        except Exception as exc:
            success = False
            message = f"跌倒检测操作失败：{exc}"
        self.fall_operation.emit(message)
        self.operation_event.emit(message)
        self.lifecycle_completed.emit(area, success)

    @staticmethod
    def _fall_event_dict(message) -> dict:
        return {
            "event_id": message.event_id,
            "mode": message.mode,
            "device_name": message.device_name,
            "occurred_at_ms": int(message.occurred_at_ms),
            "received_at_ms": int(message.received_at_ms),
            "notify_not_before_ms": int(message.notify_not_before_ms),
            "status": message.status,
            "stage": message.stage,
            "message": message.message,
            "local_confidence": float(message.local_confidence),
            "vlm_confidence": float(message.vlm_confidence),
            "assessment_reason": message.assessment_reason,
            "degraded": bool(message.degraded),
            "degraded_reason": message.degraded_reason,
            "notification_claimed": bool(message.notification_claimed),
            "notification_attempted": bool(message.notification_attempted),
            "notification_success": bool(message.notification_success),
            "text_success": bool(message.text_success),
            "image_success": bool(message.image_success),
            "updated_at_ms": int(message.updated_at_ms),
        }

    def _request_fall_events(self, limit: int) -> None:
        if not self._list_fall_events_client.wait_for_service(timeout_sec=0.25):
            self.fall_operation.emit("跌倒事件列表服务尚未连接")
            return
        request = self._list_fall_events_type.Request()
        request.limit = max(1, min(200, int(limit)))
        self._list_fall_events_client.call_async(request).add_done_callback(
            self._fall_events_received
        )

    def _fall_events_received(self, future) -> None:
        try:
            response = future.result()
            if not response.success:
                raise RuntimeError(response.message)
            events = [self._fall_event_dict(item) for item in response.events]
        except Exception as exc:
            self.fall_operation.emit(f"读取跌倒事件失败：{exc}")
            return
        self.fall_events.emit(events)

    def _request_fall_event(self, event_id: str) -> None:
        if not event_id or not self._get_fall_event_client.wait_for_service(timeout_sec=0.25):
            self.fall_operation.emit("跌倒事件详情服务尚未连接")
            return
        request = self._get_fall_event_type.Request()
        request.event_id = event_id
        self._get_fall_event_client.call_async(request).add_done_callback(
            self._fall_event_received
        )

    def _fall_event_received(self, future) -> None:
        try:
            response = future.result()
            if not response.success:
                raise RuntimeError(response.message)
            detail = self._fall_event_dict(response.event)
            detail["transitions"] = [
                {
                    "from_status": item.from_status,
                    "to_status": item.to_status,
                    "stage": item.stage,
                    "message": item.message,
                    "created_at_ms": int(item.created_at_ms),
                }
                for item in response.transitions
            ]
        except Exception as exc:
            self.fall_operation.emit(f"读取跌倒事件详情失败：{exc}")
            return
        self.fall_event_detail.emit(detail)

    def _get_front_camera_parameters(self) -> None:
        if not self._front_camera_get_client.wait_for_service(timeout_sec=0.25):
            self.front_camera_configured.emit(False, "车头相机参数服务尚未连接")
            return
        request = self._get_parameters_type.Request()
        request.names = [
            "manual_exposure",
            "exposure_time_absolute",
            "camera_gain",
            "automatic_white_balance",
            "white_balance_temperature",
        ]
        self._front_camera_get_client.call_async(request).add_done_callback(
            self._front_camera_parameters_received
        )

    def _front_camera_parameters_received(self, future) -> None:
        try:
            values = future.result().values
            result = {
                "automatic": not bool(values[0].bool_value),
                "exposure": int(values[1].integer_value),
                "gain": int(values[2].integer_value),
                "automatic_white_balance": bool(values[3].bool_value),
                "white_balance_temperature": int(values[4].integer_value),
            }
        except Exception as exc:
            self.front_camera_configured.emit(False, f"读取车头相机参数失败：{exc}")
            return
        self.front_camera_parameters.emit(result)

    def _set_front_camera_parameters(
        self,
        automatic: bool,
        exposure: int,
        gain: int,
        automatic_white_balance: bool,
        white_balance_temperature: int,
    ) -> None:
        if not self._front_camera_set_client.wait_for_service(timeout_sec=0.25):
            self.front_camera_configured.emit(False, "车头相机参数服务尚未连接")
            return
        request = self._set_parameters_type.Request()
        request.parameters = [
            self._parameter_type("manual_exposure", value=not automatic).to_parameter_msg(),
            self._parameter_type("exposure_time_absolute", value=exposure).to_parameter_msg(),
            self._parameter_type("camera_gain", value=gain).to_parameter_msg(),
            self._parameter_type(
                "automatic_white_balance", value=automatic_white_balance
            ).to_parameter_msg(),
            self._parameter_type(
                "white_balance_temperature", value=white_balance_temperature
            ).to_parameter_msg(),
        ]
        self._front_camera_set_client.call_async(request).add_done_callback(
            self._front_camera_parameters_set
        )

    def _front_camera_parameters_set(self, future) -> None:
        try:
            results = future.result().results
            failures = [item.reason for item in results if not item.successful]
        except Exception as exc:
            self.front_camera_configured.emit(False, f"应用车头相机参数失败：{exc}")
            return
        if failures:
            self.front_camera_configured.emit(False, "；".join(failures))
            return
        self.front_camera_configured.emit(True, "车头相机曝光参数已立即应用")
        self._get_front_camera_parameters()

    def _switch_voice(self, backend: int) -> None:
        if not self._switch_voice_client.wait_for_server(timeout_sec=0.25):
            self._set_voice_control_ready(False, "未发现 Orin 语音控制；请检查局域网和 ROS Domain 42")
            self._emit_voice_operation("语音切换服务尚未连接")
            return
        self._set_voice_control_ready(True, "Orin 语音控制已连接")
        goal = self._switch_voice_type.Goal()
        goal.backend = backend
        future = self._switch_voice_client.send_goal_async(
            goal,
            feedback_callback=lambda message: self._emit_voice_operation(
                f"语音切换：{message.feedback.message}"
            ),
        )
        future.add_done_callback(self._voice_goal_response)

    def _probe_voice_control(self) -> None:
        ready = self._switch_voice_client.wait_for_server(timeout_sec=0.35)
        self._set_voice_control_ready(
            ready,
            "Orin 语音控制已连接"
            if ready
            else "未发现 Orin 语音控制；请检查局域网和 ROS Domain 42",
            force=True,
        )

    def _check_voice_control(self) -> None:
        self._set_voice_control_ready(
            bool(self._switch_voice_client.server_is_ready()),
            "Orin 语音控制已连接"
            if self._switch_voice_client.server_is_ready()
            else "等待发现 Orin 语音控制",
        )
        manipulation_ready = bool(
            self._start_visual_grasp_client.service_is_ready()
            and self._stop_visual_grasp_client.service_is_ready()
        )
        if manipulation_ready != self._manipulation_control_ready:
            self._manipulation_control_ready = manipulation_ready
            self.manipulation_control_available.emit(
                manipulation_ready,
                "Orin 机械臂生命周期控制已连接"
                if manipulation_ready
                else "等待发现 Orin 机械臂生命周期控制",
            )
        fall_ready = bool(
            self._start_fall_client.service_is_ready()
            and self._stop_fall_client.service_is_ready()
            and self._restart_fall_client.service_is_ready()
        )
        if fall_ready != self._fall_control_ready:
            self._fall_control_ready = fall_ready
            self.fall_control_available.emit(
                fall_ready,
                "Orin 跌倒检测控制已连接"
                if fall_ready
                else "等待发现 Orin 跌倒检测控制",
            )

    def _set_voice_control_ready(self, ready: bool, message: str, force: bool = False) -> None:
        ready = bool(ready)
        if force or ready != self._voice_control_ready:
            self._voice_control_ready = ready
            self.voice_control_available.emit(ready, message)

    def _emit_voice_operation(self, message: str) -> None:
        self.voice_operation.emit(message)
        self.operation_event.emit(message)

    def _voice_goal_response(self, future) -> None:
        try:
            handle = future.result()
        except Exception as exc:
            self._emit_voice_operation(f"语音切换失败：{exc}")
            return
        if not handle.accepted:
            self._emit_voice_operation("语音切换请求被拒绝")
            return
        handle.get_result_async().add_done_callback(self._voice_result)

    def _voice_result(self, future) -> None:
        try:
            result = future.result().result
            self._emit_voice_operation(result.message)
            self.lifecycle_completed.emit("voice", bool(result.success))
        except Exception as exc:
            self._emit_voice_operation(f"语音切换结果读取失败：{exc}")
            self.lifecycle_completed.emit("voice", False)

    def _set_uwb_shadow(self, enabled: bool) -> None:
        if not self._uwb_enabled:
            self.operation_event.emit("UWB 已从当前 MVP 隐藏")
            return
        client = self._start_uwb_client if enabled else self._stop_uwb_client
        if not client.wait_for_service(timeout_sec=0.0):
            self.operation_event.emit("UWB shadow 管理服务尚未连接")
            return
        client.call_async(self._trigger_type.Request()).add_done_callback(self._trigger_done)

    def _trigger_done(self, future) -> None:
        try:
            response = future.result()
            self.operation_event.emit(response.message)
        except Exception as exc:
            self.operation_event.emit(f"远程服务调用失败：{exc}")

    def _manage_feedback(self, feedback_message) -> None:
        feedback = feedback_message.feedback
        self.operation_event.emit(f"{feedback.step}: {feedback.message}")
        self.stack_progress.emit(
            {
                "state": "running",
                "step": feedback.step,
                "progress": float(feedback.progress),
                "message": feedback.message,
            }
        )

    def _manage_goal_response(self, future) -> None:
        try:
            handle = future.result()
        except Exception as exc:
            self._pending_manage = None
            self.operation_event.emit(f"模式切换失败：{exc}")
            self.stack_progress.emit(
                {"state": "failed", "progress": 0.0, "message": f"模式切换失败：{exc}"}
            )
            self.lifecycle_completed.emit("stack", False)
            return
        if not handle.accepted:
            self._pending_manage = None
            self.operation_event.emit("模式切换请求被拒绝")
            self.stack_progress.emit(
                {"state": "failed", "progress": 0.0, "message": "模式切换请求被拒绝"}
            )
            self.lifecycle_completed.emit("stack", False)
            return
        result_future = handle.get_result_async()
        result_future.add_done_callback(self._manage_result)

    def _manage_result(self, future) -> None:
        self._pending_manage = None
        try:
            result = future.result().result
            success = bool(result.success)
            message = "操作已完成" if success else result.message
        except Exception as exc:
            success = False
            message = f"模式切换结果读取失败：{exc}"
        self.operation_event.emit(message)
        self.stack_progress.emit(
            {
                "state": "complete" if success else "failed",
                "progress": 1.0 if success else 0.0,
                "message": message,
            }
        )
        self.lifecycle_completed.emit("stack", success)

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
        self._last_mode = int(message.mode)
        if not self._connected:
            self._connected = True
            self._connection_text = "Orin 已连接"
            self.connection_changed.emit(True, self._connection_text)
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
        self._reconcile_pending_manage(self._last_mode)

    def _reconcile_pending_manage(self, mode: int) -> None:
        pending = self._pending_manage
        if pending is None:
            return
        # MODE_TRANSITIONING is 4 in the typed SystemState contract.
        if mode == 4:
            pending["saw_transitioning"] = True
            return
        if pending["restart"] and not pending["saw_transitioning"]:
            return
        expected_modes = {
            1: {1},       # start mapping
            2: {2},       # start Navigation2
            3: {3},       # start rf2o fallback
            4: {0, 1},    # stop Nav2, preserve mapping when available
            5: {0},       # stop all
        }
        if mode not in expected_modes.get(int(pending["operation"]), set()):
            return
        self._pending_manage = None
        message = "Orin 当前系统状态已确认目标模式"
        self.operation_event.emit(message)
        self.stack_progress.emit(
            {
                "state": "complete",
                "step": "状态确认",
                "progress": 1.0,
                "message": message,
            }
        )
        self.lifecycle_completed.emit("stack", True)

    def _check_state_freshness(self) -> None:
        if self._connected and time.monotonic() - self._last_state_monotonic > 2.0:
            self._connected = False
            self._connection_text = "Orin 状态已超时"
            self.connection_changed.emit(False, self._connection_text)

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

    def _on_voice_status(self, message) -> None:
        raw = str(message.data)
        if raw.lstrip().startswith("{"):
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                value = {"backend": "unknown", "state": "invalid"}
            value["state"] = (
                "conversation_active"
                if value.get("conversation_active")
                else ("idle" if value.get("session_ready", True) else "connecting")
            )
            value["wakeup_state"] = (
                "已唤醒"
                if value.get("conversation_active")
                else (
                    "唤醒串口异常"
                    if value.get("wakeup_serial_state") == "fault"
                    else (
                        "等待唤醒"
                        if value.get("wakeup_serial_state") == "ready"
                        else "正在连接唤醒串口"
                    )
                )
            )
        else:
            state = raw.split(";", 1)[0].strip() or "unknown"
            value = {
                "backend": "classic",
                "state": state,
                "conversation_active": state == "conversation_active",
                "pending_task": state.removeprefix("awaiting_confirmation_")
                if state.startswith("awaiting_confirmation_") else "",
                "active_task": state.removeprefix("executing_")
                if state.startswith("executing_") else "",
                "wakeup_state": "已唤醒" if state == "conversation_active" else "等待唤醒",
            }
        value["raw"] = raw
        self.voice_status.emit(value)

    def _on_fall_status(self, message) -> None:
        self.fall_status.emit(
            {
                "scan_mode": message.scan_mode,
                "service_ready": bool(message.service_ready),
                "event_active": bool(message.event_active),
                "active_event_id": message.active_event_id,
                "stage": message.stage,
                "scan_step": int(message.scan_step),
                "scan_total": int(message.scan_total),
                "current_heading_deg": float(message.current_heading_deg),
                "target_heading_deg": float(message.target_heading_deg),
                "local_confidence": float(message.local_confidence),
                "vlm_confidence": float(message.vlm_confidence),
                "motion_active": bool(message.motion_active),
                "camera_ready": bool(message.camera_ready),
                "specialized_model_ready": bool(message.specialized_model_ready),
                "world_model_ready": bool(message.world_model_ready),
                "vlm_ready": bool(message.vlm_ready),
                "notification_ready": bool(message.notification_ready),
                "nav2_action_ready": bool(message.nav2_action_ready),
                "nav2_lifecycle_ready": bool(message.nav2_lifecycle_ready),
                "tf_ready": bool(message.tf_ready),
                "odom_ready": bool(message.odom_ready),
                "costmap_ready": bool(message.costmap_ready),
                "rotation_clear": bool(message.rotation_clear),
                "cmd_vel_clear": bool(message.cmd_vel_clear),
                "arm_safe": bool(message.arm_safe),
                "message": message.message,
            }
        )

    def _on_uwb_observation(self, message) -> None:
        self.uwb_observation.emit(
            {
                "source_id": message.source_id,
                "tag_time_raw": int(message.tag_time_raw),
                "x_m": float(message.x_m),
                "y_m": float(message.y_m),
                "range_m": float(message.range_m),
                "coordinate_range_m": float(message.coordinate_range_m),
                "range_residual_m": float(message.range_residual_m),
                "valid": bool(message.valid),
                "rejection_reason": message.rejection_reason,
            }
        )

    def _on_uwb_status(self, message) -> None:
        self.uwb_status.emit(str(message.data))

    def _on_uwb_goal(self, message) -> None:
        self.uwb_goal.emit(
            {
                "frame_id": message.header.frame_id,
                "x": float(message.pose.position.x),
                "y": float(message.pose.position.y),
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
        self._publish_lidar_calibration_cloud(message)
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

    def _sync_cloud_subscription(self) -> None:
        wanted = self._cloud_enabled or self._lidar_calibration_enabled
        if wanted and self._cloud_subscription is None:
            self._cloud_subscription = self._node.create_subscription(
                self._point_cloud_type,
                "/unilidar/cloud",
                self._on_cloud,
                self._sensor_qos,
            )
        elif not wanted and self._cloud_subscription is not None:
            self._node.destroy_subscription(self._cloud_subscription)
            self._cloud_subscription = None

    def _publish_lidar_calibration_cloud(self, message) -> None:
        if not self._lidar_calibration_enabled:
            return
        roll, pitch, yaw = self._lidar_preview_rpy
        mount = _quaternion_from_rpy(roll, pitch, yaw)
        driver = _quaternion_from_rpy(math.pi, 0.0, 2.0112063268)
        qx, qy, qz, qw = _multiply_quaternions(mount, driver)
        transform = self._transform_stamped_type()
        transform.header.stamp = message.header.stamp
        transform.header.frame_id = "base_link"
        transform.child_frame_id = self._lidar_preview_frame
        transform.transform.translation.x = 0.190
        transform.transform.translation.y = 0.0
        transform.transform.translation.z = 0.550
        transform.transform.rotation.x = qx
        transform.transform.rotation.y = qy
        transform.transform.rotation.z = qz
        transform.transform.rotation.w = qw
        self._lidar_preview_tf.sendTransform(transform)

        preview = self._point_cloud_type()
        preview.header.stamp = message.header.stamp
        preview.header.frame_id = self._lidar_preview_frame
        preview.height = message.height
        preview.width = message.width
        preview.fields = message.fields
        preview.is_bigendian = message.is_bigendian
        preview.point_step = message.point_step
        preview.row_step = message.row_step
        preview.data = message.data
        preview.is_dense = message.is_dense
        self._lidar_preview_pub.publish(preview)

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
        self._front_camera_timer.stop()
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
