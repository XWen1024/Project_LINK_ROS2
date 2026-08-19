"""Offline UI data source for laptop-only console development."""

from __future__ import annotations

import math

from PySide6.QtCore import QObject, QTimer, Signal

from .models import GridLayer, Pose2D


class DemoBridge(QObject):
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
    uwb_observation = Signal(dict)
    uwb_status = Signal(str)
    uwb_goal = Signal(dict)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._mode = 1
        self._robot = Pose2D(1.0, 1.0, 0.2)
        self._path: list[tuple[float, float]] = []
        self._cloud_enabled = False
        self._lidar_preview_right_rad = 0.0
        self._voice_backend = "off"
        self._uwb_shadow = False
        self._tick_count = 0
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self.connection_changed.emit(True, "离线演示")
        self.voice_control_available.emit(True, "离线演示语音控制已连接")
        self.manipulation_control_available.emit(True, "离线演示机械臂控制已连接")
        QTimer.singleShot(0, self._emit_map)
        QTimer.singleShot(0, self._emit_state)
        self._timer.start()

    def connection_snapshot(self) -> tuple[bool, str]:
        return False, "等待离线演示启动"

    def _emit_map(self) -> None:
        width, height = 120, 90
        cells = [0] * (width * height)
        for row in range(height):
            for column in range(width):
                wall = row in (0, height - 1) or column in (0, width - 1)
                wall = wall or (column == 55 and 15 < row < 72 and not 40 < row < 48)
                wall = wall or (row == 28 and 12 < column < 48)
                if wall:
                    cells[row * width + column] = 100
        grid = GridLayer(width, height, 0.05, -1.0, -0.5, tuple(cells))
        self.grid_updated.emit("occupancy_map", grid)
        local = [0] * (40 * 40)
        for index in range(12, 28):
            local[25 * 40 + index] = 100
        self.grid_updated.emit("local_costmap", GridLayer(40, 40, 0.05, 0.0, 0.0, tuple(local)))

    def _emit_state(self) -> None:
        active = self._mode != 0
        state_names = {0: "off", 1: "mapping", 2: "navigation", 3: "rf2o_fallback"}
        units = [
            ("底盘串口", active),
            ("Unitree L1 雷达", active),
            ("Point-LIO 建图", self._mode in (1, 2)),
            ("Navigation2", self._mode == 2),
            ("机械臂服务", False),
            ("project-link-voice-classic.service", self._voice_backend == "classic"),
            ("project-link-voice-qwen.service", self._voice_backend == "qwen_realtime"),
            ("project-link-uwb-shadow.service", self._uwb_shadow),
        ]
        self.system_state.emit(
            {
                "mode": self._mode,
                "mode_name": state_names.get(self._mode, "unknown"),
                "emergency_stop_latched": False,
                "teleop_active": False,
                "voice_backend": self._voice_backend,
                "message": "demo",
                "subsystems": [
                    {
                        "name": name,
                        "display_name": name,
                        "active_state": "active" if ready else "inactive",
                        "sub_state": "running" if ready else "dead",
                        "ready": ready,
                        "severity": 0 if ready else 1,
                        "message": "演示数据",
                    }
                    for name, ready in units
                ],
            }
        )

    def manage_stack(self, operation: int, restart: bool = False) -> None:
        del restart
        self.stack_progress.emit(
            {"state": "running", "step": "demo", "progress": 0.5, "message": "正在切换演示模式"}
        )
        if operation == 1:
            self._mode = 1
        elif operation == 2:
            self._mode = 2
        elif operation == 3:
            self._mode = 3
        elif operation == 4:
            self._mode = 1
        elif operation == 5:
            self._mode = 0
        self.operation_event.emit("演示模式已切换")
        self.stack_progress.emit(
            {"state": "complete", "step": "complete", "progress": 1.0, "message": "演示模式已切换"}
        )
        self.lifecycle_completed.emit("stack", True)
        self._emit_state()

    def send_teleop(self, enabled: bool, deadman: bool, linear: float, angular: float) -> None:
        if not (enabled and deadman and self._mode == 1):
            return
        dt = 0.05
        yaw = self._robot.yaw + angular * dt
        self._robot = Pose2D(
            self._robot.x + math.cos(yaw) * linear * dt,
            self._robot.y + math.sin(yaw) * linear * dt,
            yaw,
        )
        self.robot_updated.emit(self._robot)

    def send_navigation_goal(self, pose: Pose2D) -> None:
        if self._mode != 2:
            self.operation_event.emit("演示模式：请先启动 Navigation2")
            return
        steps = 40
        self._path = [
            (
                self._robot.x + (pose.x - self._robot.x) * index / steps,
                self._robot.y + (pose.y - self._robot.y) * index / steps,
            )
            for index in range(steps + 1)
        ]
        self.path_updated.emit(self._path)
        self.operation_event.emit("演示导航目标已接受")

    def emergency_stop(self) -> None:
        self._path.clear()
        self.path_updated.emit([])
        self.operation_event.emit("演示紧急停车已锁定")

    def switch_voice(self, backend: int) -> None:
        self._voice_backend = {0: "off", 1: "classic", 2: "qwen_realtime"}.get(backend, "off")
        self.operation_event.emit("演示语音后端已切换")
        self.voice_operation.emit("演示语音后端已切换")
        self._emit_state()
        if self._voice_backend != "off":
            self.voice_status.emit(
                {
                    "backend": self._voice_backend,
                    "state": "idle",
                    "wakeup_state": "等待唤醒",
                    "conversation_active": False,
                    "pending_task": "",
                    "active_task": "",
                    "raw": "demo",
                }
            )

    def probe_voice_control(self) -> None:
        self.voice_control_available.emit(True, "离线演示语音控制已连接")

    def start_visual_grasp(self) -> None:
        self.manipulation_control_available.emit(True, "离线演示机械臂控制已连接")
        self.manipulation_operation.emit("演示机械臂服务已启动")
        self.lifecycle_completed.emit("manipulation", True)

    def stop_visual_grasp(self) -> None:
        self.manipulation_operation.emit("演示机械臂服务已停止")
        self.lifecycle_completed.emit("manipulation", True)

    def request_front_camera_parameters(self) -> None:
        self.front_camera_parameters.emit(
            {"automatic": False, "exposure": 300, "gain": 32}
        )

    def set_front_camera_exposure(self, automatic: bool, exposure: int, gain: int) -> None:
        self.front_camera_parameters.emit(
            {"automatic": automatic, "exposure": exposure, "gain": gain}
        )
        self.front_camera_configured.emit(True, "演示相机参数已应用")

    def start_uwb_shadow(self) -> None:
        self._uwb_shadow = True
        self.uwb_status.emit("demo_shadow_running")
        self.operation_event.emit("演示 UWB shadow 已启动")
        self._emit_state()

    def stop_uwb_shadow(self) -> None:
        self._uwb_shadow = False
        self.uwb_status.emit("demo_shadow_stopped")
        self.operation_event.emit("演示 UWB shadow 已停止")
        self._emit_state()

    def set_cloud_enabled(self, enabled: bool) -> None:
        self._cloud_enabled = bool(enabled)

    def set_lidar_preview_rotation(self, clockwise_degrees: float) -> None:
        self._lidar_preview_right_rad = math.radians(float(clockwise_degrees))

    def _tick(self) -> None:
        self._tick_count += 1
        if self._path:
            x, y = self._path.pop(0)
            self._robot = Pose2D(x, y, self._robot.yaw)
            self.robot_updated.emit(self._robot)
            self.path_updated.emit(self._path)
        angle = self._robot.yaw
        scan = []
        for index in range(160):
            theta = angle + index * math.tau / 160.0
            radius = 0.9 + 0.08 * math.sin(index * 0.31)
            scan.append((self._robot.x + radius * math.cos(theta), self._robot.y + radius * math.sin(theta)))
        if abs(self._lidar_preview_right_rad) > 1.0e-8:
            angle = -self._lidar_preview_right_rad
            cosine = math.cos(angle)
            sine = math.sin(angle)
            scan = [
                (
                    self._robot.x
                    + cosine * (x - self._robot.x)
                    - sine * (y - self._robot.y),
                    self._robot.y
                    + sine * (x - self._robot.x)
                    + cosine * (y - self._robot.y),
                )
                for x, y in scan
            ]
        self.scan_updated.emit(scan)
        if self._cloud_enabled:
            self.cloud_updated.emit(scan[::4])
        self.robot_updated.emit(self._robot)
        if self._voice_backend != "off" and self._tick_count % 20 == 0:
            self.console_event.emit(
                {
                    "severity": 0,
                    "subsystem": "voice",
                    "phase": "demo_idle",
                    "delta_ms": 42.0,
                    "total_ms": 42.0,
                    "message": "等待唤醒",
                }
            )
        if self._uwb_shadow:
            angle = 0.7 + 0.35 * math.sin(self._tick_count * 0.04)
            distance = 2.0 + 0.15 * math.sin(self._tick_count * 0.07)
            x = distance * math.cos(angle)
            y = distance * math.sin(angle)
            self.uwb_observation.emit(
                {
                    "source_id": "demo-tag",
                    "tag_time_raw": self._tick_count,
                    "x_m": x,
                    "y_m": y,
                    "range_m": distance + 0.03,
                    "coordinate_range_m": distance,
                    "range_residual_m": 0.03,
                    "valid": True,
                    "rejection_reason": "",
                }
            )

    def stop(self) -> None:
        self._timer.stop()
