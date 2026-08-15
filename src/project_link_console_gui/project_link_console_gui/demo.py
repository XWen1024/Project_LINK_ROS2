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
    connection_changed = Signal(bool, str)
    operation_event = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._mode = 1
        self._robot = Pose2D(1.0, 1.0, 0.2)
        self._path: list[tuple[float, float]] = []
        self._cloud_enabled = False
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self.connection_changed.emit(True, "离线演示")
        QTimer.singleShot(0, self._emit_map)
        QTimer.singleShot(0, self._emit_state)
        self._timer.start()

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
            ("语音服务", False),
        ]
        self.system_state.emit(
            {
                "mode": self._mode,
                "mode_name": state_names.get(self._mode, "unknown"),
                "emergency_stop_latched": False,
                "teleop_active": False,
                "voice_backend": "off",
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

    def set_cloud_enabled(self, enabled: bool) -> None:
        self._cloud_enabled = bool(enabled)

    def _tick(self) -> None:
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
        self.scan_updated.emit(scan)
        if self._cloud_enabled:
            self.cloud_updated.emit(scan[::4])
        self.robot_updated.emit(self._robot)

    def stop(self) -> None:
        self._timer.stop()
