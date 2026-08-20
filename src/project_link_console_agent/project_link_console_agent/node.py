"""ROS 2 console agent for systemd lifecycle and fail-closed teleoperation."""

from __future__ import annotations

import json
from pathlib import Path
import threading
import time

import rclpy
from geometry_msgs.msg import Twist
from rclpy.action import ActionServer
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from std_msgs.msg import String
from std_srvs.srv import Trigger

from project_link_console_interfaces.action import ManageStack, SwitchVoice
from project_link_console_interfaces.msg import (
    ConsoleEvent,
    SubsystemState,
    SystemState,
    TeleopCommand,
)

from .systemd import SystemdManager, UNITS
from .teleop import TeleopLease
from .voice_state import VoiceState, active_voice_backend, parse_voice_status


SUBSYSTEM_LABELS = {
    UNITS["agent"]: "中控通信代理",
    UNITS["base"]: "底盘串口与里程计",
    UNITS["lidar"]: "Unitree L1 三维激光雷达",
    UNITS["front_camera"]: "车头 720P 摄像头",
    UNITS["fall_response"]: "跌倒检测响应",
    UNITS["wechatbot"]: "微信紧急通知",
    UNITS["robot_description"]: "机器人模型与传感器坐标",
    UNITS["scan"]: "二维雷达扫描",
    UNITS["point_lio_map"]: "Point-LIO 定位与实时建图",
    UNITS["nav2"]: "Navigation2 路径规划与导航",
    UNITS["rf2o"]: "rf2o 备用定位",
    UNITS["visual_grasp"]: "机械臂视觉抓取服务",
    UNITS["visual_grasp_detector"]: "机械臂 CUDA 目标检测",
    UNITS["vl53l0x"]: "夹爪距离传感器",
    UNITS["voice_classic"]: "经典语音链路",
    UNITS["voice_qwen"]: "Qwen Realtime 语音",
    UNITS["uwb_shadow"]: "UWB 影子模式",
    UNITS["platform_target"]: "底盘与传感器基础平台",
    UNITS["mapping_target"]: "建图模式总流程",
    UNITS["navigation_target"]: "Navigation2 模式总流程",
    UNITS["rf2o_target"]: "rf2o 备用模式总流程",
    UNITS["emergency_target"]: "紧急响应总流程",
}


class ConsoleAgent(Node):
    def __init__(self) -> None:
        super().__init__("project_link_console_agent")
        self.declare_parameter("systemctl_command", "systemctl")
        self.declare_parameter("cmd_vel_topic", "/cmd_vel")
        self.declare_parameter("teleop_timeout_sec", 0.25)
        self.declare_parameter("teleop_max_linear_mps", 0.18)
        self.declare_parameter("teleop_max_angular_rps", 0.60)
        self.declare_parameter("state_rate_hz", 2.0)
        self.declare_parameter(
            "classic_voice_timing_file",
            "~/.ros/project_link_voice/voice_timing.jsonl",
        )
        self.declare_parameter(
            "qwen_voice_timing_file",
            "~/.ros/project_link_qwen_realtime/voice_timing.jsonl",
        )

        self._callbacks = ReentrantCallbackGroup()
        self._systemd = SystemdManager(str(self.get_parameter("systemctl_command").value))
        self._lock = threading.RLock()
        self._voice_lifecycle_lock = threading.Lock()
        self._voice = VoiceState()
        self._emergency_latched = False
        self._mode = SystemState.MODE_OFF
        self._mode_name = "off"
        self._teleop = TeleopLease(
            timeout_sec=float(self.get_parameter("teleop_timeout_sec").value),
            max_linear_mps=float(self.get_parameter("teleop_max_linear_mps").value),
            max_angular_rps=float(self.get_parameter("teleop_max_angular_rps").value),
        )
        self._teleop_publisher = None
        self._teleop_was_active = False
        self._voice_timing_paths = {
            "classic": Path(str(self.get_parameter("classic_voice_timing_file").value)).expanduser(),
            "qwen_realtime": Path(str(self.get_parameter("qwen_voice_timing_file").value)).expanduser(),
        }
        self._voice_timing_offsets = {
            backend: path.stat().st_size if path.is_file() else 0
            for backend, path in self._voice_timing_paths.items()
        }

        self._state_pub = self.create_publisher(SystemState, "/project_link/console/system_state", 10)
        self._event_pub = self.create_publisher(ConsoleEvent, "/project_link/console/events", 20)
        self.create_subscription(
            TeleopCommand,
            "/project_link/console/teleop",
            self._on_teleop,
            20,
            callback_group=self._callbacks,
        )
        self.create_subscription(String, "/voice/status", self._on_voice_status, 10)
        self.create_service(
            Trigger,
            "/project_link/console/emergency_stop",
            self._emergency_stop,
            callback_group=self._callbacks,
        )
        self.create_service(
            Trigger,
            "/project_link/console/clear_emergency_stop",
            self._clear_emergency_stop,
            callback_group=self._callbacks,
        )
        self.create_service(
            Trigger,
            "/project_link/console/start_visual_grasp",
            self._start_visual_grasp,
            callback_group=self._callbacks,
        )
        self.create_service(
            Trigger,
            "/project_link/console/stop_visual_grasp",
            self._stop_visual_grasp,
            callback_group=self._callbacks,
        )
        self.create_service(
            Trigger,
            "/project_link/console/start_fall_response",
            self._start_fall_response,
            callback_group=self._callbacks,
        )
        self.create_service(
            Trigger,
            "/project_link/console/stop_fall_response",
            self._stop_fall_response,
            callback_group=self._callbacks,
        )
        self.create_service(
            Trigger,
            "/project_link/console/restart_fall_response",
            self._restart_fall_response,
            callback_group=self._callbacks,
        )
        self.create_service(
            Trigger,
            "/project_link/console/restart_wechatbot",
            self._restart_wechatbot,
            callback_group=self._callbacks,
        )
        self.create_service(
            Trigger,
            "/project_link/console/start_uwb_shadow",
            self._start_uwb_shadow,
            callback_group=self._callbacks,
        )
        self.create_service(
            Trigger,
            "/project_link/console/stop_uwb_shadow",
            self._stop_uwb_shadow,
            callback_group=self._callbacks,
        )
        self._stack_server = ActionServer(
            self,
            ManageStack,
            "/project_link/console/manage_stack",
            execute_callback=self._manage_stack,
            callback_group=self._callbacks,
        )
        self._voice_server = ActionServer(
            self,
            SwitchVoice,
            "/project_link/console/switch_voice",
            execute_callback=self._switch_voice,
            callback_group=self._callbacks,
        )
        state_period = 1.0 / max(0.2, float(self.get_parameter("state_rate_hz").value))
        self.create_timer(state_period, self._publish_state, callback_group=self._callbacks)
        self.create_timer(0.05, self._teleop_tick, callback_group=self._callbacks)
        self.create_timer(0.20, self._poll_voice_timing, callback_group=self._callbacks)
        self.get_logger().info("Project LINK console agent started; no stack or motion was started.")

    def _emit(self, subsystem: str, message: str, severity: int = ConsoleEvent.SEVERITY_INFO) -> None:
        event = ConsoleEvent()
        event.stamp = self.get_clock().now().to_msg()
        event.severity = severity
        event.subsystem = subsystem
        event.message = message
        self._event_pub.publish(event)

    def _on_voice_status(self, message: String) -> None:
        with self._lock:
            self._voice = parse_voice_status(message.data)

    def _poll_voice_timing(self) -> None:
        with self._lock:
            backend = self._voice.backend
        path = self._voice_timing_paths.get(backend)
        if path is None or not path.is_file():
            return
        offset = self._voice_timing_offsets.get(backend, 0)
        try:
            size = path.stat().st_size
            if size < offset:
                offset = 0
            with path.open("rb") as stream:
                stream.seek(offset)
                data = stream.read(262144)
        except OSError:
            return
        if not data:
            return
        complete = data.rfind(b"\n")
        if complete < 0:
            return
        self._voice_timing_offsets[backend] = offset + complete + 1
        for raw_line in data[:complete].splitlines():
            try:
                row = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if row.get("kind") not in {"timing", "timing_summary"}:
                continue
            event = ConsoleEvent()
            event.stamp = self.get_clock().now().to_msg()
            event.severity = ConsoleEvent.SEVERITY_INFO
            event.subsystem = "voice"
            event.trace_id = str(row.get("trace_id", ""))
            event.phase = str(row.get("phase", "summary"))
            event.delta_ms = float(
                row.get("delta_ms", row.get("step_delta_ms", row.get("phase_elapsed_ms", 0.0)))
            )
            event.total_ms = float(row.get("total_ms", row.get("trace_total_ms", row.get("elapsed_ms", 0.0))))
            details = []
            for key in ("tool", "success", "source", "response_id"):
                if key in row:
                    details.append(f"{key}={row[key]}")
            event.message = " ".join(details)
            self._event_pub.publish(event)

    def _on_teleop(self, message: TeleopCommand) -> None:
        with self._lock:
            self._teleop.update(
                enabled=message.enabled,
                deadman=message.deadman,
                linear_x=message.linear_x,
                angular_z=message.angular_z,
                sequence=message.sequence,
                now=time.monotonic(),
            )
            if not message.enabled:
                self._stop_teleop_locked("teleop_disabled")

    def _ensure_teleop_publisher_locked(self):
        if self._teleop_publisher is None:
            self._teleop_publisher = self.create_publisher(
                Twist,
                str(self.get_parameter("cmd_vel_topic").value),
                10,
            )
        return self._teleop_publisher

    def _publish_twist_locked(self, linear_x: float, angular_z: float) -> None:
        publisher = self._ensure_teleop_publisher_locked()
        message = Twist()
        message.linear.x = float(linear_x)
        message.angular.z = float(angular_z)
        publisher.publish(message)

    def _stop_teleop_locked(self, reason: str) -> None:
        if self._teleop_publisher is not None:
            for _attempt in range(3):
                self._publish_twist_locked(0.0, 0.0)
                time.sleep(0.02)
            self.destroy_publisher(self._teleop_publisher)
            self._teleop_publisher = None
        if self._teleop_was_active:
            self._emit("teleop", reason)
        self._teleop_was_active = False

    def _unexpected_cmd_vel_publishers(self) -> list[str]:
        topic = str(self.get_parameter("cmd_vel_topic").value)
        names = []
        for endpoint in self.get_publishers_info_by_topic(topic):
            if endpoint.node_name != self.get_name():
                names.append(endpoint.node_name)
        return sorted(set(names))

    def _teleop_tick(self) -> None:
        with self._lock:
            active = self._teleop.active(
                time.monotonic(),
                mapping_mode=self._mode == SystemState.MODE_MAPPING,
                emergency_latched=self._emergency_latched,
            )
            if active:
                unexpected = self._unexpected_cmd_vel_publishers()
                if unexpected:
                    self._stop_teleop_locked("unexpected_cmd_vel_publishers")
                    self._emit("teleop", f"Blocked by publishers: {unexpected}", ConsoleEvent.SEVERITY_ERROR)
                    return
                self._publish_twist_locked(self._teleop.linear_x, self._teleop.angular_z)
                self._teleop_was_active = True
            else:
                self._stop_teleop_locked("deadman_or_lease_lost")

    def _derive_mode(self, states: dict[str, object]) -> tuple[int, str]:
        if states[UNITS["nav2"]].active:
            return SystemState.MODE_NAVIGATION, "navigation"
        if states[UNITS["point_lio_map"]].active:
            return SystemState.MODE_MAPPING, "mapping"
        if states[UNITS["rf2o"]].active:
            return SystemState.MODE_RF2O_FALLBACK, "rf2o_fallback"
        return SystemState.MODE_OFF, "off"

    def _publish_state(self) -> None:
        states = self._systemd.safe_states(UNITS.values())
        mode, mode_name = self._derive_mode(states)
        with self._lock:
            self._mode = mode
            self._mode_name = mode_name
            voice = self._voice
            emergency = self._emergency_latched
            teleop_active = self._teleop_was_active
        message = SystemState()
        message.stamp = self.get_clock().now().to_msg()
        message.mode = mode
        message.mode_name = mode_name
        message.emergency_stop_latched = emergency
        message.teleop_active = teleop_active
        message.voice_backend = active_voice_backend(
            states[UNITS["voice_classic"]].active,
            states[UNITS["voice_qwen"]].active,
        )
        message.message = "emergency_stop_latched" if emergency else "ready"
        for unit, state in states.items():
            item = SubsystemState()
            item.name = unit
            item.display_name = SUBSYSTEM_LABELS.get(unit, state.description or unit)
            item.active_state = state.active_state
            item.sub_state = state.sub_state
            item.result = state.result
            item.restart_count = state.restart_count
            item.ready = state.active
            item.stale = state.active_state == "unknown"
            if item.stale:
                item.severity = SubsystemState.SEVERITY_STALE
            elif state.active_state == "failed" or state.result not in ("success", "unknown", ""):
                item.severity = SubsystemState.SEVERITY_ERROR
            elif state.active:
                item.severity = SubsystemState.SEVERITY_OK
            else:
                item.severity = SubsystemState.SEVERITY_WARN
            item.message = state.result
            message.subsystems.append(item)
        self._state_pub.publish(message)

    @staticmethod
    def _feedback(goal_handle, step: str, progress: float, message: str) -> None:
        feedback = ManageStack.Feedback()
        feedback.step = step
        feedback.progress = progress
        feedback.message = message
        goal_handle.publish_feedback(feedback)

    def _start_target_with_progress(
        self,
        goal_handle,
        target: str,
        ordered_units: list[str],
        *,
        restart: bool,
        timeout_sec: float = 210.0,
    ) -> None:
        if restart:
            self._systemd.restart_no_block(target)
        else:
            self._systemd.start_no_block(target)
        deadline = time.monotonic() + timeout_sec
        last_snapshot = None
        next_target_reactivation = 0.0
        while time.monotonic() < deadline:
            states = self._systemd.safe_states(ordered_units)
            dependencies_ready = all(
                states[unit].active for unit in ordered_units if unit != target
            )
            failed = [
                state
                for state in states.values()
                if state.active_state == "failed" or state.sub_state == "failed"
                if not (state.unit == target and dependencies_ready)
            ]
            if failed:
                state = failed[0]
                label = SUBSYSTEM_LABELS.get(state.unit, state.unit)
                raise RuntimeError(f"{label}启动失败：{state.result}")
            target_state = states[target]
            now = time.monotonic()
            if (
                dependencies_ready
                and not target_state.active
                and now >= next_target_reactivation
            ):
                # A required service may recover through Restart=on-failure after
                # systemd has already marked the original target job failed. In
                # that state Nav2 and both costmaps are healthy, but the target
                # remains inactive forever unless it is explicitly started again.
                self._feedback(
                    goal_handle,
                    target,
                    0.96,
                    "依赖功能已就绪，正在重新确认模式状态",
                )
                self._systemd.start_no_block(target)
                next_target_reactivation = now + 2.0
            ready_count = sum(1 for unit in ordered_units if states[unit].active)
            pending = next(
                (unit for unit in ordered_units if not states[unit].active),
                ordered_units[-1],
            )
            pending_state = states[pending]
            snapshot = (ready_count, pending, pending_state.active_state, pending_state.sub_state)
            if snapshot != last_snapshot:
                label = SUBSYSTEM_LABELS.get(pending, pending)
                if pending_state.active_state == "activating":
                    description = f"{label}：正在执行启动与就绪检查"
                elif pending_state.active_state == "inactive":
                    description = f"{label}：等待前置功能就绪"
                else:
                    description = f"{label}：{pending_state.active_state}/{pending_state.sub_state}"
                self._feedback(
                    goal_handle,
                    pending,
                    min(0.95, 0.12 + 0.83 * ready_count / max(1, len(ordered_units))),
                    description,
                )
                last_snapshot = snapshot
            if ready_count == len(ordered_units):
                return
            time.sleep(0.5)
        pending_labels = [
            SUBSYSTEM_LABELS.get(unit, unit)
            for unit, state in self._systemd.safe_states(ordered_units).items()
            if not state.active
        ]
        raise RuntimeError("启动超时，仍未就绪：" + "、".join(pending_labels))

    def _manage_stack(self, goal_handle) -> ManageStack.Result:
        request = goal_handle.request
        result = ManageStack.Result()
        final_mode = SystemState.MODE_OFF
        final_mode_name = "off"
        with self._lock:
            if self._emergency_latched and request.operation not in (
                ManageStack.Goal.OPERATION_STOP_NAVIGATION,
                ManageStack.Goal.OPERATION_STOP_ALL,
            ):
                goal_handle.abort()
                result.success = False
                result.final_mode = self._mode
                result.message = "emergency_stop_latched"
                return result
            self._teleop.clear()
            self._stop_teleop_locked("stack_transition")
            self._mode = SystemState.MODE_TRANSITIONING
            self._mode_name = "transitioning"
        try:
            self._feedback(goal_handle, "prepare", 0.05, "正在停止不兼容的控制链路")
            if request.operation == ManageStack.Goal.OPERATION_START_MAPPING:
                self._systemd.stop(UNITS["navigation_target"])
                self._systemd.stop(UNITS["rf2o_target"])
                if request.restart:
                    self._systemd.stop(UNITS["mapping_target"])
                    self._start_target_with_progress(
                        goal_handle,
                        UNITS["platform_target"],
                        [
                            UNITS["base"],
                            UNITS["lidar"],
                            UNITS["robot_description"],
                            UNITS["scan"],
                            UNITS["platform_target"],
                        ],
                        restart=True,
                    )
                self._start_target_with_progress(
                    goal_handle,
                    UNITS["mapping_target"],
                    [
                        UNITS["base"],
                        UNITS["lidar"],
                        UNITS["robot_description"],
                        UNITS["scan"],
                        UNITS["platform_target"],
                        UNITS["point_lio_map"],
                        UNITS["mapping_target"],
                    ],
                    restart=False,
                )
                final_mode = SystemState.MODE_MAPPING
                final_mode_name = "mapping"
            elif request.operation == ManageStack.Goal.OPERATION_START_NAVIGATION:
                self._systemd.stop(UNITS["rf2o_target"])
                if request.restart:
                    self._systemd.stop(UNITS["navigation_target"])
                    self._systemd.stop(UNITS["mapping_target"])
                    self._start_target_with_progress(
                        goal_handle,
                        UNITS["platform_target"],
                        [
                            UNITS["base"],
                            UNITS["lidar"],
                            UNITS["robot_description"],
                            UNITS["scan"],
                            UNITS["platform_target"],
                        ],
                        restart=True,
                    )
                self._start_target_with_progress(
                    goal_handle,
                    UNITS["navigation_target"],
                    [
                        UNITS["base"],
                        UNITS["lidar"],
                        UNITS["robot_description"],
                        UNITS["scan"],
                        UNITS["platform_target"],
                        UNITS["point_lio_map"],
                        UNITS["mapping_target"],
                        UNITS["nav2"],
                        UNITS["navigation_target"],
                    ],
                    restart=False,
                )
                final_mode = SystemState.MODE_NAVIGATION
                final_mode_name = "navigation"
            elif request.operation == ManageStack.Goal.OPERATION_START_RF2O_FALLBACK:
                self._systemd.stop(UNITS["navigation_target"])
                self._systemd.stop(UNITS["mapping_target"])
                self._feedback(goal_handle, "rf2o", 0.5, "Starting rf2o fallback target")
                self._systemd.restart(UNITS["rf2o_target"]) if request.restart else self._systemd.start(UNITS["rf2o_target"])
                final_mode = SystemState.MODE_RF2O_FALLBACK
                final_mode_name = "rf2o_fallback"
            elif request.operation == ManageStack.Goal.OPERATION_STOP_NAVIGATION:
                self._systemd.stop(UNITS["navigation_target"])
                mapping_state = self._systemd.safe_state(UNITS["point_lio_map"])
                if mapping_state.active:
                    final_mode = SystemState.MODE_MAPPING
                    final_mode_name = "mapping"
            elif request.operation == ManageStack.Goal.OPERATION_STOP_ALL:
                self._systemd.stop(UNITS["navigation_target"])
                self._systemd.stop(UNITS["mapping_target"])
                self._systemd.stop(UNITS["rf2o_target"])
                self._systemd.stop(UNITS["platform_target"])
            else:
                raise ValueError("unsupported_stack_operation")
        except Exception as exc:
            with self._lock:
                self._mode = SystemState.MODE_FAULT
                self._mode_name = "fault"
            goal_handle.abort()
            result.success = False
            result.final_mode = SystemState.MODE_FAULT
            result.message = str(exc)
            self._emit("lifecycle", str(exc), ConsoleEvent.SEVERITY_ERROR)
            return result
        with self._lock:
            self._mode = final_mode
            self._mode_name = final_mode_name
        self._feedback(goal_handle, "complete", 1.0, "启动流程已完成")
        goal_handle.succeed()
        result.success = True
        result.final_mode = final_mode
        result.message = "operation_completed"
        self._emit("lifecycle", result.message)
        return result

    def _switch_voice(self, goal_handle) -> SwitchVoice.Result:
        request = goal_handle.request
        result = SwitchVoice.Result()
        if not self._voice_lifecycle_lock.acquire(blocking=False):
            goal_handle.abort()
            result.success = False
            states = self._systemd.safe_states(
                [UNITS["voice_classic"], UNITS["voice_qwen"]]
            )
            if states[UNITS["voice_qwen"]].active:
                result.active_backend = SwitchVoice.Goal.BACKEND_QWEN_REALTIME
            elif states[UNITS["voice_classic"]].active:
                result.active_backend = SwitchVoice.Goal.BACKEND_CLASSIC
            else:
                result.active_backend = SwitchVoice.Goal.BACKEND_OFF
            result.message = "voice_switch_already_in_progress"
            return result
        try:
            return self._switch_voice_locked(goal_handle, request, result)
        finally:
            self._voice_lifecycle_lock.release()

    def _switch_voice_locked(self, goal_handle, request, result) -> SwitchVoice.Result:
        with self._lock:
            current = self._voice
        voice_units = self._systemd.safe_states(
            [UNITS["voice_classic"], UNITS["voice_qwen"]]
        )
        any_voice_active = any(state.active for state in voice_units.values())
        if (
            request.backend != SwitchVoice.Goal.BACKEND_OFF
            and any_voice_active
            and not current.idle
        ):
            goal_handle.abort()
            result.success = False
            result.active_backend = SwitchVoice.Goal.BACKEND_OFF
            result.message = "voice_not_idle"
            return result
        try:
            feedback = SwitchVoice.Feedback()
            feedback.step = "submit"
            feedback.progress = 0.20
            feedback.message = "正在提交语音后端切换"
            goal_handle.publish_feedback(feedback)
            with self._lock:
                self._voice = VoiceState()
            if request.backend == SwitchVoice.Goal.BACKEND_CLASSIC:
                self._systemd.reset_failed(UNITS["voice_classic"])
                self._systemd.start_no_block(UNITS["voice_classic"])
            elif request.backend == SwitchVoice.Goal.BACKEND_QWEN_REALTIME:
                self._systemd.reset_failed(UNITS["voice_qwen"])
                self._systemd.start_no_block(UNITS["voice_qwen"])
            elif request.backend == SwitchVoice.Goal.BACKEND_OFF:
                self._systemd.stop_no_block(UNITS["voice_classic"])
                self._systemd.stop_no_block(UNITS["voice_qwen"])
            elif request.backend != SwitchVoice.Goal.BACKEND_OFF:
                raise ValueError("unsupported_voice_backend")
            feedback.step = "queued"
            feedback.progress = 0.85
            feedback.message = "systemd 已接收请求，后台状态将自动刷新"
            goal_handle.publish_feedback(feedback)
        except Exception as exc:
            with self._lock:
                self._voice = VoiceState()
            goal_handle.abort()
            result.success = False
            result.active_backend = SwitchVoice.Goal.BACKEND_OFF
            result.message = str(exc)
            self._emit("voice", str(exc), ConsoleEvent.SEVERITY_ERROR)
            return result
        goal_handle.succeed()
        result.success = True
        result.active_backend = request.backend
        result.message = "voice_backend_switched"
        self._emit("voice", result.message)
        return result

    def _start_visual_grasp(self, _request, response):
        try:
            self._systemd.start(UNITS["visual_grasp"])
        except Exception as exc:
            response.success = False
            response.message = str(exc)
            self._emit("manipulation", response.message, ConsoleEvent.SEVERITY_ERROR)
            return response
        response.success = True
        response.message = "机械臂视觉服务已启动；尚未连接机械臂或启用扭矩"
        self._emit("manipulation", response.message)
        return response

    def _stop_visual_grasp(self, _request, response):
        try:
            self._systemd.stop(UNITS["visual_grasp"])
        except Exception as exc:
            response.success = False
            response.message = str(exc)
            self._emit("manipulation", response.message, ConsoleEvent.SEVERITY_ERROR)
            return response
        response.success = True
        response.message = "机械臂视觉服务已停止"
        self._emit("manipulation", response.message)
        return response

    def _start_fall_response(self, _request, response):
        try:
            self._systemd.start(UNITS["emergency_target"])
        except Exception as exc:
            response.success = False
            response.message = str(exc)
            self._emit("fall_response", response.message, ConsoleEvent.SEVERITY_ERROR)
            return response
        response.success = True
        response.message = "跌倒检测服务已启动；不会自动启动 Nav2 或产生运动"
        self._emit("fall_response", response.message)
        return response

    def _stop_fall_response(self, _request, response):
        try:
            self._systemd.stop(UNITS["emergency_target"])
        except Exception as exc:
            response.success = False
            response.message = str(exc)
            self._emit("fall_response", response.message, ConsoleEvent.SEVERITY_ERROR)
            return response
        response.success = True
        response.message = "跌倒检测与微信通知服务已停止"
        self._emit("fall_response", response.message)
        return response

    def _restart_fall_response(self, _request, response):
        try:
            self._systemd.restart(UNITS["emergency_target"])
        except Exception as exc:
            response.success = False
            response.message = str(exc)
            self._emit("fall_response", response.message, ConsoleEvent.SEVERITY_ERROR)
            return response
        response.success = True
        response.message = "跌倒检测服务已重启并重新读取 Orin 配置"
        self._emit("fall_response", response.message)
        return response

    def _restart_wechatbot(self, _request, response):
        try:
            self._systemd.restart(UNITS["wechatbot"])
        except Exception as exc:
            response.success = False
            response.message = str(exc)
            self._emit("fall_response", response.message, ConsoleEvent.SEVERITY_ERROR)
            return response
        response.success = True
        response.message = "微信通知服务已重启"
        self._emit("fall_response", response.message)
        return response

    def _emergency_stop(self, _request, response):
        with self._lock:
            self._emergency_latched = True
            self._teleop.clear()
            self._stop_teleop_locked("emergency_stop")
            # Always create a short-lived publisher for the stop burst. The
            # regular teleop publisher may already have been destroyed while
            # Nav2 or another controller was active.
            for _attempt in range(5):
                self._publish_twist_locked(0.0, 0.0)
                time.sleep(0.02)
            if self._teleop_publisher is not None:
                self.destroy_publisher(self._teleop_publisher)
                self._teleop_publisher = None
        errors = []
        for unit in (UNITS["navigation_target"], UNITS["uwb_shadow"]):
            try:
                self._systemd.stop(unit)
            except Exception as exc:
                errors.append(str(exc))
        response.success = not errors
        response.message = "emergency_stop_latched" if not errors else "; ".join(errors)
        self._emit("safety", response.message, ConsoleEvent.SEVERITY_ERROR)
        return response

    def _start_uwb_shadow(self, _request, response):
        try:
            self._systemd.start(UNITS["uwb_shadow"])
        except Exception as exc:
            response.success = False
            response.message = str(exc)
            self._emit("uwb", response.message, ConsoleEvent.SEVERITY_ERROR)
            return response
        response.success = True
        response.message = "uwb_shadow_started"
        self._emit("uwb", response.message)
        return response

    def _stop_uwb_shadow(self, _request, response):
        try:
            self._systemd.stop(UNITS["uwb_shadow"])
        except Exception as exc:
            response.success = False
            response.message = str(exc)
            self._emit("uwb", response.message, ConsoleEvent.SEVERITY_ERROR)
            return response
        response.success = True
        response.message = "uwb_shadow_stopped"
        self._emit("uwb", response.message)
        return response

    def _clear_emergency_stop(self, _request, response):
        nav_state = self._systemd.safe_state(UNITS["nav2"])
        if nav_state.active:
            response.success = False
            response.message = "nav2_still_active"
            return response
        with self._lock:
            self._emergency_latched = False
        response.success = True
        response.message = "emergency_stop_cleared"
        self._emit("safety", response.message)
        return response

    def destroy_node(self):
        with self._lock:
            self._teleop.clear()
            self._stop_teleop_locked("agent_shutdown")
        self._stack_server.destroy()
        self._voice_server.destroy()
        return super().destroy_node()


def main() -> None:
    rclpy.init()
    node = ConsoleAgent()
    executor = MultiThreadedExecutor(num_threads=4)
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
