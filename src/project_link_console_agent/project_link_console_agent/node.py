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
from .voice_state import VoiceState, parse_voice_status


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
        message.voice_backend = voice.backend
        message.message = "emergency_stop_latched" if emergency else "ready"
        for unit, state in states.items():
            item = SubsystemState()
            item.name = unit
            item.display_name = state.description or unit
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
            self._feedback(goal_handle, "prepare", 0.1, "Stopping incompatible control paths")
            if request.operation == ManageStack.Goal.OPERATION_START_MAPPING:
                self._systemd.stop(UNITS["navigation_target"])
                self._systemd.stop(UNITS["rf2o_target"])
                self._feedback(goal_handle, "mapping", 0.5, "Starting Point-LIO mapping target")
                self._systemd.restart(UNITS["mapping_target"]) if request.restart else self._systemd.start(UNITS["mapping_target"])
                final_mode = SystemState.MODE_MAPPING
                final_mode_name = "mapping"
            elif request.operation == ManageStack.Goal.OPERATION_START_NAVIGATION:
                self._systemd.stop(UNITS["rf2o_target"])
                self._feedback(goal_handle, "navigation", 0.5, "Starting navigation target")
                self._systemd.restart(UNITS["navigation_target"]) if request.restart else self._systemd.start(UNITS["navigation_target"])
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
        goal_handle.succeed()
        result.success = True
        result.final_mode = final_mode
        result.message = "operation_completed"
        self._emit("lifecycle", result.message)
        return result

    def _switch_voice(self, goal_handle) -> SwitchVoice.Result:
        request = goal_handle.request
        result = SwitchVoice.Result()
        with self._lock:
            current = self._voice
        if request.backend != SwitchVoice.Goal.BACKEND_OFF and not current.idle:
            goal_handle.abort()
            result.success = False
            result.active_backend = SwitchVoice.Goal.BACKEND_OFF
            result.message = "voice_not_idle"
            return result
        try:
            feedback = SwitchVoice.Feedback()
            feedback.step = "stop_existing"
            feedback.progress = 0.25
            feedback.message = "Stopping current voice backend"
            goal_handle.publish_feedback(feedback)
            self._systemd.stop(UNITS["voice_classic"])
            self._systemd.stop(UNITS["voice_qwen"])
            if request.backend == SwitchVoice.Goal.BACKEND_CLASSIC:
                self._systemd.start(UNITS["voice_classic"])
            elif request.backend == SwitchVoice.Goal.BACKEND_QWEN_REALTIME:
                self._systemd.start(UNITS["voice_qwen"])
            elif request.backend != SwitchVoice.Goal.BACKEND_OFF:
                raise ValueError("unsupported_voice_backend")
        except Exception as exc:
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
