"""Python-owned ROS tool execution and safety gates."""

from __future__ import annotations

import json
import math
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import rclpy
from action_msgs.msg import GoalStatus
from geometry_msgs.msg import PoseStamped, Twist
from nav2_msgs.action import NavigateToPose
from rclpy.action import ActionClient
from std_srvs.srv import SetBool, Trigger
from tf2_ros import Buffer, TransformException, TransformListener

from project_link_voice.task_parser import parse_aliases
from project_link_voice.waypoints import Waypoint, WaypointStore
from project_link_voice_interfaces.action import DriveToPoint
from wheeltec_robot_msg.action import TrackAndGrasp

from .tools import PendingTask, ToolExecutionResult
from .weather import query_current_weather


class RobotToolController:
    def __init__(self, node, share_dir: Path, speak: Callable[[str], None]) -> None:
        self._node = node
        self._speak = speak
        self._target_frame = str(node.get_parameter("target_frame").value).lstrip("/")
        self._base_frame = str(node.get_parameter("base_frame").value).lstrip("/")
        override = str(node.get_parameter("waypoints_override_file").value).strip()
        self._waypoints = WaypointStore(
            share_dir / "data" / "default_waypoints.json",
            Path(override).expanduser() if override else None,
        )
        self._item_aliases = parse_aliases(list(node.get_parameter("grasp_target_aliases").value))
        self._tf_buffer = Buffer()
        self._tf_listener = TransformListener(self._tf_buffer, node)
        self._drive_client = ActionClient(node, DriveToPoint, "/voice/drive_to_point")
        self._nav2_client = ActionClient(
            node,
            NavigateToPose,
            str(node.get_parameter("nav2_action_name").value),
        )
        self._grasp_client = ActionClient(
            node,
            TrackAndGrasp,
            str(node.get_parameter("visual_grasp_action_name").value),
        )
        self._connect_arm = node.create_client(
            Trigger,
            str(node.get_parameter("visual_grasp_connect_service").value),
        )
        self._set_torque = node.create_client(
            SetBool,
            str(node.get_parameter("visual_grasp_torque_service").value),
        )
        self._stop_grasp = node.create_client(
            Trigger,
            str(node.get_parameter("visual_grasp_stop_service").value),
        )
        self._demo_pub = None
        if bool(node.get_parameter("enable_demo_motion").value):
            self._demo_pub = node.create_publisher(
                Twist,
                str(node.get_parameter("demo_cmd_vel_topic").value),
                10,
            )
        self.pending_task: PendingTask | None = None
        self.active_task: PendingTask | None = None
        self._goal_handle = None
        self._grasp_goal_handle = None
        self._navigation_started_at = 0.0
        self._demo_thread: threading.Thread | None = None
        self._demo_stop = threading.Event()
        self.map_seen = False
        self.scan_seen = False
        self.odom_seen = False

    @property
    def waypoint_names(self) -> list[str]:
        return self._waypoints.names()

    def execute(self, name: str, args: dict[str, Any]) -> ToolExecutionResult:
        if name == "get_weather":
            return ToolExecutionResult(self._get_weather(args))
        if name == "get_current_location":
            return ToolExecutionResult(self._get_current_location())
        if name == "save_waypoint":
            return ToolExecutionResult(self._save_waypoint(args))
        if name == "list_saved_locations":
            return ToolExecutionResult({"success": True, "locations": self._waypoints.names()})
        if name == "cancel_current_task":
            self.cancel_everything("qwen tool cancellation")
            return ToolExecutionResult(
                {"success": True, "message": "已取消当前任务。", "spoken_reply": "已取消当前任务。"},
                spoken_reply="已取消当前任务。",
            )
        if name == "navigate_to_location":
            return self._prepare_navigation(args)
        if name == "fetch_item_from_location":
            return self._prepare_fetch(args)
        if name == "demo_motion":
            return self._demo_motion(args)
        return ToolExecutionResult({"success": False, "message": f"未知工具：{name}"})

    def confirm_pending(self) -> str:
        task = self.pending_task
        self.pending_task = None
        if task is None:
            return "当前没有等待确认的任务。"
        if self._pure_test_mode():
            return f"纯测试模式已确认{task.waypoint.name}任务，不会发送底盘或机械臂动作。"
        ready, reason = self._slam_ready()
        if not ready:
            return f"拒绝启动，{reason}。"
        if not bool(self._node.get_parameter("enable_motion").value):
            return f"Dry-run 已确认{task.waypoint.name}任务，未启用运动。"
        navigation_ready, navigation_reason = self._navigation_ready()
        if not navigation_ready:
            return f"拒绝启动，{navigation_reason}。"
        if self.active_task is not None:
            return "当前已有任务正在执行，拒绝启动新任务。"
        self.active_task = task
        self._navigation_started_at = time.monotonic()
        if str(self._node.get_parameter("navigation_backend").value) == "nav2":
            self._send_nav2_goal(task)
        else:
            self._send_drive_goal(task)
        return task.immediate_reply or f"已确认前往{task.waypoint.name}。"

    def confirmation_expired(self) -> bool:
        if self.pending_task is None:
            return False
        timeout = float(self._node.get_parameter("confirmation_timeout_sec").value)
        if time.monotonic() - self.pending_task.created_at <= timeout:
            return False
        self.pending_task = None
        return True

    def navigation_timed_out(self) -> bool:
        if self.active_task is None or self._navigation_started_at <= 0:
            return False
        timeout = float(self._node.get_parameter("navigation_timeout_sec").value)
        if time.monotonic() - self._navigation_started_at <= timeout:
            return False
        self.cancel_everything("navigation timeout")
        return True

    def cancel_everything(self, reason: str) -> None:
        self._node.get_logger().warn(f"Canceling robot work: {reason}")
        self.pending_task = None
        self.active_task = None
        self._navigation_started_at = 0.0
        if self._goal_handle is not None:
            self._goal_handle.cancel_goal_async()
            self._goal_handle = None
        if self._grasp_goal_handle is not None:
            self._grasp_goal_handle.cancel_goal_async()
            self._grasp_goal_handle = None
        if self._stop_grasp.service_is_ready():
            self._stop_grasp.call_async(Trigger.Request())
        self._stop_demo_motion()

    def _current_pose(self) -> tuple[float, float, float] | None:
        try:
            transform = self._tf_buffer.lookup_transform(
                self._target_frame,
                self._base_frame,
                rclpy.time.Time(),
            )
        except TransformException:
            return None
        translation = transform.transform.translation
        rotation = transform.transform.rotation
        yaw = math.atan2(
            2.0 * (rotation.w * rotation.z + rotation.x * rotation.y),
            1.0 - 2.0 * (rotation.y * rotation.y + rotation.z * rotation.z),
        )
        return translation.x, translation.y, yaw

    def _get_current_location(self) -> dict[str, Any]:
        pose = self._current_pose()
        if pose is None:
            return {"success": False, "message": "当前 map 位姿不可用。"}
        x, y, yaw = pose
        return {"success": True, "frame": self._target_frame, "x": x, "y": y, "yaw": yaw}

    def _save_waypoint(self, args: dict[str, Any]) -> dict[str, Any]:
        name = str(args.get("location_name", "")).strip()
        if not name:
            return {"success": False, "message": "地点名称不能为空。"}
        pose = self._current_pose()
        if pose is None:
            return {"success": False, "message": "当前 map 位姿不可用，无法保存。"}
        try:
            self._waypoints.save(name, *pose)
        except Exception as exc:
            return {"success": False, "message": f"保存失败：{exc}"}
        return {"success": True, "message": f"已保存地点：{name}。"}

    def _get_weather(self, args: dict[str, Any]) -> dict[str, Any]:
        city = str(args.get("city_name", "")).strip()
        api_key = os.environ.get("QWEATHER_API_KEY", "").strip()
        api_host = os.environ.get("QWEATHER_API_HOST", "").strip()
        try:
            return query_current_weather(city, api_key, api_host)
        except Exception as exc:
            return {"success": False, "message": f"天气查询失败：{exc}"}

    def _resolve_waypoint(self, args: dict[str, Any]) -> Waypoint | None:
        name = str(args.get("target_name") or args.get("location_name") or "").strip()
        return self._waypoints.get(name) if name else None

    def _prepare_navigation(self, args: dict[str, Any]) -> ToolExecutionResult:
        if self.active_task is not None:
            return ToolExecutionResult({"success": False, "message": "已有任务正在执行。"})
        waypoint = self._resolve_waypoint(args)
        if waypoint is None:
            return ToolExecutionResult({"success": False, "message": "目标不是已保存航点。"})
        self.pending_task = PendingTask.now(
            kind="navigate",
            waypoint=waypoint,
            immediate_reply=str(args.get("immediate_reply") or f"已确认前往{waypoint.name}。"),
            arrival_reply=str(args.get("arrival_reply") or f"已到达{waypoint.name}。"),
        )
        spoken = self._confirmation_prompt(f"准备前往{waypoint.name}。", False)
        return ToolExecutionResult(
            {
                "success": True,
                "pending": "navigation",
                "target_name": waypoint.name,
                "spoken_reply": spoken,
            },
            spoken_reply=spoken,
            requires_confirmation=True,
        )

    def _prepare_fetch(self, args: dict[str, Any]) -> ToolExecutionResult:
        if self.active_task is not None:
            return ToolExecutionResult({"success": False, "message": "已有任务正在执行。"})
        waypoint = self._resolve_waypoint(args)
        if waypoint is None:
            return ToolExecutionResult({"success": False, "message": "抓取地点不是已保存航点。"})
        item_name = str(args.get("item_name") or "").strip()
        grasp_target = str(args.get("grasp_target") or "").strip()
        if not grasp_target and item_name:
            grasp_target = self._item_aliases.get(item_name, item_name)
        if not item_name or not grasp_target:
            return ToolExecutionResult({"success": False, "message": "缺少物品名称或视觉目标。"})
        timeout_value = args.get("timeout_sec")
        timeout_sec = float(timeout_value) if isinstance(timeout_value, (int, float)) else None
        self.pending_task = PendingTask.now(
            kind="fetch",
            waypoint=waypoint,
            item_name=item_name,
            grasp_target=grasp_target,
            grasp_timeout_sec=timeout_sec,
            immediate_reply=str(args.get("immediate_reply") or f"已确认前往{waypoint.name}抓取{item_name}。"),
            arrival_reply=str(args.get("arrival_reply") or f"已到达{waypoint.name}，准备抓取{item_name}。"),
            success_reply=str(args.get("success_reply") or f"{item_name}抓取成功。"),
            failure_reply=str(args.get("failure_reply") or f"{item_name}抓取失败。"),
        )
        spoken = self._confirmation_prompt(
            f"准备前往{waypoint.name}抓取{item_name}。",
            True,
        )
        return ToolExecutionResult(
            {
                "success": True,
                "pending": "fetch",
                "target_name": waypoint.name,
                "grasp_target": grasp_target,
                "spoken_reply": spoken,
            },
            spoken_reply=spoken,
            requires_confirmation=True,
        )

    def _confirmation_prompt(self, prefix: str, include_arm: bool) -> str:
        backend = str(self._node.get_parameter("navigation_backend").value)
        if backend == "nav2":
            warning = "将使用 Navigation2 规划和避障，但仍要确认通道清空、有人监护并且急停可用。"
        else:
            warning = "这是无规划、无避障的低速直驱，必须确认路径清空、有人监护并且急停可用。"
        if include_arm:
            warning += "还必须确认机械臂区域安全。"
        return prefix + warning + "请说确认开始，或说取消。"

    def _pure_test_mode(self) -> bool:
        mode = str(self._node.get_parameter("pure_test_mode").value).strip().lower()
        if mode in ("on", "true", "1"):
            return True
        if mode in ("off", "false", "0"):
            return False
        return not (self.map_seen or self.scan_seen or self.odom_seen)

    def _slam_ready(self) -> tuple[bool, str]:
        if not self.map_seen:
            return False, "等待 /map"
        if not self.scan_seen:
            return False, "等待 /scan"
        if not self.odom_seen:
            return False, "等待 /odom"
        if self._current_pose() is None:
            return False, f"等待 TF {self._target_frame}->{self._base_frame}"
        return True, "SLAM 和 TF 已就绪"

    def _navigation_ready(self) -> tuple[bool, str]:
        backend = str(self._node.get_parameter("navigation_backend").value)
        if backend == "direct_drive":
            return (
                (True, "直驱服务器已就绪")
                if self._drive_client.wait_for_server(timeout_sec=0.0)
                else (False, "直驱服务器未就绪")
            )
        if not self._nav2_client.wait_for_server(timeout_sec=0.0):
            return False, "Nav2 Action 未就绪"
        allowed = {
            str(name).strip().lstrip("/")
            for name in self._node.get_parameter("nav2_allowed_cmd_vel_publishers").value
        }
        unexpected = sorted(
            {
                str(info.node_name).lstrip("/")
                for info in self._node.get_publishers_info_by_topic(
                    str(self._node.get_parameter("nav2_cmd_vel_topic").value)
                )
                if str(info.node_name).lstrip("/") not in allowed
            }
        )
        if unexpected:
            return False, f"检测到非 Nav2 的 cmd_vel 发布者：{unexpected}"
        return True, "Nav2 已就绪"

    def _goal_pose(self, waypoint: Waypoint) -> PoseStamped:
        pose = PoseStamped()
        pose.header.frame_id = self._target_frame
        pose.header.stamp = self._node.get_clock().now().to_msg()
        pose.pose.position.x = waypoint.x
        pose.pose.position.y = waypoint.y
        pose.pose.orientation.z = math.sin(waypoint.yaw / 2.0)
        pose.pose.orientation.w = math.cos(waypoint.yaw / 2.0)
        return pose

    def _send_drive_goal(self, task: PendingTask) -> None:
        goal = DriveToPoint.Goal()
        goal.target = self._goal_pose(task.waypoint)
        future = self._drive_client.send_goal_async(goal)
        future.add_done_callback(self._on_navigation_goal)

    def _send_nav2_goal(self, task: PendingTask) -> None:
        goal = NavigateToPose.Goal()
        goal.pose = self._goal_pose(task.waypoint)
        goal.behavior_tree = str(self._node.get_parameter("nav2_behavior_tree").value).strip()
        future = self._nav2_client.send_goal_async(goal)
        future.add_done_callback(self._on_navigation_goal)

    def _on_navigation_goal(self, future) -> None:
        try:
            self._goal_handle = future.result()
        except Exception as exc:
            self._finish_task(f"导航目标发送失败：{exc}")
            return
        if self._goal_handle is None or not self._goal_handle.accepted:
            self._finish_task("导航目标被拒绝，机器人没有运动。")
            return
        result = self._goal_handle.get_result_async()
        result.add_done_callback(self._on_navigation_result)

    def _on_navigation_result(self, future) -> None:
        task = self.active_task
        try:
            wrapped = future.result()
            succeeded = wrapped.status == GoalStatus.STATUS_SUCCEEDED
        except Exception as exc:
            self._finish_task(f"导航结果异常：{exc}")
            return
        self._goal_handle = None
        self._navigation_started_at = 0.0
        if not succeeded or task is None:
            self._finish_task("导航没有成功，后续抓取不会执行。")
            return
        if task.kind == "fetch":
            self._speak(task.arrival_reply)
            self._prepare_grasp(task)
        else:
            self._finish_task(task.arrival_reply or f"已到达{task.waypoint.name}。")

    def _prepare_grasp(self, task: PendingTask) -> None:
        if not bool(self._node.get_parameter("enable_visual_grasp").value):
            self._finish_task("视觉抓取未启用，任务停在目标位置。")
            return
        if not self._grasp_client.wait_for_server(timeout_sec=0.0):
            self._finish_task("视觉抓取 Action 未就绪。")
            return
        if not bool(self._node.get_parameter("visual_grasp_prepare_arm").value):
            self._send_grasp_goal(task)
            return
        if not self._connect_arm.wait_for_service(timeout_sec=0.0):
            self._finish_task("机械臂连接服务未就绪。")
            return
        future = self._connect_arm.call_async(Trigger.Request())
        future.add_done_callback(lambda result: self._on_arm_connected(result, task))

    def _on_arm_connected(self, future, task: PendingTask) -> None:
        response = future.result()
        if response is None or not response.success:
            self._finish_task("机械臂连接失败。")
            return
        if not self._set_torque.wait_for_service(timeout_sec=0.0):
            self._finish_task("机械臂扭矩服务未就绪。")
            return
        request = SetBool.Request()
        request.data = True
        future = self._set_torque.call_async(request)
        future.add_done_callback(lambda result: self._on_torque_enabled(result, task))

    def _on_torque_enabled(self, future, task: PendingTask) -> None:
        response = future.result()
        if response is None or not response.success:
            self._finish_task("机械臂扭矩启用失败。")
            return
        self._send_grasp_goal(task)

    def _send_grasp_goal(self, task: PendingTask) -> None:
        goal = TrackAndGrasp.Goal()
        goal.target = task.grasp_target
        goal.timeout_sec = float(
            task.grasp_timeout_sec
            or self._node.get_parameter("visual_grasp_timeout_sec").value
        )
        future = self._grasp_client.send_goal_async(goal)
        future.add_done_callback(self._on_grasp_goal)

    def _on_grasp_goal(self, future) -> None:
        self._grasp_goal_handle = future.result()
        if self._grasp_goal_handle is None or not self._grasp_goal_handle.accepted:
            self._finish_task("视觉抓取任务被拒绝。")
            return
        result = self._grasp_goal_handle.get_result_async()
        result.add_done_callback(self._on_grasp_result)

    def _on_grasp_result(self, future) -> None:
        task = self.active_task
        self._grasp_goal_handle = None
        try:
            result = future.result().result
            success = bool(result.success)
            message = str(result.message)
        except Exception as exc:
            self._finish_task(f"视觉抓取结果异常：{exc}")
            return
        if task is None:
            return
        reply = task.success_reply if success else task.failure_reply
        self._finish_task(reply or message)

    def _finish_task(self, reply: str) -> None:
        self.active_task = None
        self._goal_handle = None
        self._grasp_goal_handle = None
        self._navigation_started_at = 0.0
        if reply:
            self._speak(reply)

    def _demo_motion(self, args: dict[str, Any]) -> ToolExecutionResult:
        if self._demo_pub is None:
            return ToolExecutionResult({"success": False, "message": "演示运动未启用。"})
        direction = str(args.get("direction", "")).strip().lower()
        mapping = {
            "forward": ("前进一点", float(self._node.get_parameter("demo_linear_mps").value), 0.0, "demo_step_sec"),
            "backward": ("后退一点", -float(self._node.get_parameter("demo_linear_mps").value), 0.0, "demo_step_sec"),
            "left": ("左转一点", 0.0, float(self._node.get_parameter("demo_angular_rps").value), "demo_turn_sec"),
            "right": ("右转一点", 0.0, -float(self._node.get_parameter("demo_angular_rps").value), "demo_turn_sec"),
            "spin": ("原地转一圈", 0.0, float(self._node.get_parameter("demo_angular_rps").value), "demo_spin_sec"),
        }
        if direction == "stop":
            self._stop_demo_motion()
            return ToolExecutionResult({"success": True, "message": "已停止。", "spoken_reply": "已停止。"}, "已停止。")
        if direction not in mapping:
            return ToolExecutionResult({"success": False, "message": "不支持的演示动作。"})
        label, linear, angular, duration_parameter = mapping[direction]
        duration = float(self._node.get_parameter(duration_parameter).value)
        self._stop_demo_motion()
        self._demo_stop.clear()
        self._demo_thread = threading.Thread(
            target=self._run_demo_motion,
            args=(linear, angular, duration),
            daemon=True,
        )
        self._demo_thread.start()
        spoken_reply = f"演示动作：{label}。"
        return ToolExecutionResult(
            {"success": True, "message": label, "spoken_reply": spoken_reply},
            spoken_reply,
        )

    def _run_demo_motion(self, linear: float, angular: float, duration: float) -> None:
        twist = Twist()
        twist.linear.x = linear
        twist.angular.z = angular
        started = time.monotonic()
        try:
            while not self._demo_stop.is_set() and time.monotonic() - started < duration:
                self._demo_pub.publish(twist)
                time.sleep(0.05)
        finally:
            self._publish_demo_stop()

    def _stop_demo_motion(self) -> None:
        self._demo_stop.set()
        self._publish_demo_stop()
        thread = self._demo_thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=0.5)
        self._demo_thread = None

    def _publish_demo_stop(self) -> None:
        if self._demo_pub is None:
            return
        for _ in range(5):
            self._demo_pub.publish(Twist())
            time.sleep(0.02)

    def shutdown(self) -> None:
        self.cancel_everything("node shutdown")
