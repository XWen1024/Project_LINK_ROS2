"""Navigation and mapping page for the Project LINK console."""

from __future__ import annotations

import math

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .map_view import MapView
from .models import GridLayer, Pose2D
from .teleop_pad import TeleopPad


class NavigationPage(QWidget):
    launch_rviz_requested = Signal()

    MODE_OFF = 0
    MODE_MAPPING = 1
    MODE_NAVIGATION = 2
    MODE_RF2O = 3

    def __init__(self, bridge, parent=None) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._mode = self.MODE_OFF
        self._connected = False
        self._pending_goal: Pose2D | None = None
        self._advanced = False

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        title_row = QHBoxLayout()
        title = QLabel("建图与导航")
        title.setObjectName("pageTitle")
        self.mode_badge = QLabel("系统关闭")
        self.mode_badge.setObjectName("modeBadge")
        title_row.addWidget(title)
        title_row.addWidget(self.mode_badge)
        title_row.addStretch()
        self.rviz_button = QPushButton("打开 RViz2 深度调试")
        self.rviz_button.clicked.connect(self.launch_rviz_requested)
        title_row.addWidget(self.rviz_button)
        root.addLayout(title_row)

        actions = QHBoxLayout()
        self.mapping_button = QPushButton("开始建图")
        self.navigation_button = QPushButton("开始 Navigation2")
        self.mapping_only_button = QPushButton("停止导航，保留建图")
        self.stop_button = QPushButton("全部停止")
        self.emergency_button = QPushButton("紧急停车")
        self.emergency_button.setObjectName("dangerButton")
        self.mapping_button.clicked.connect(lambda: bridge.manage_stack(1, False))
        self.navigation_button.clicked.connect(lambda: bridge.manage_stack(2, False))
        self.mapping_only_button.clicked.connect(lambda: bridge.manage_stack(4, False))
        self.stop_button.clicked.connect(lambda: bridge.manage_stack(5, False))
        self.emergency_button.clicked.connect(bridge.emergency_stop)
        for button in (
            self.mapping_button,
            self.navigation_button,
            self.mapping_only_button,
            self.stop_button,
            self.emergency_button,
        ):
            actions.addWidget(button)
        actions.addStretch()
        root.addLayout(actions)

        splitter = QSplitter(Qt.Horizontal)
        self.map_view = MapView()
        splitter.addWidget(self.map_view)
        side = QWidget()
        side.setMinimumWidth(330)
        side.setMaximumWidth(440)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(4, 0, 0, 0)

        layer_group = QGroupBox("显示图层")
        layer_layout = QGridLayout(layer_group)
        layers = [
            ("occupancy_map", "实时地图", True),
            ("global_costmap", "全局代价地图", False),
            ("local_costmap", "局部代价地图", True),
            ("laser_scan", "雷达扫描", True),
            ("point_cloud", "雷达点云", False),
            ("path", "导航路径", True),
        ]
        self.layer_checks: dict[str, QCheckBox] = {}
        for index, (name, label, checked) in enumerate(layers):
            checkbox = QCheckBox(label)
            checkbox.setChecked(checked)
            checkbox.toggled.connect(
                lambda visible, layer=name: self.map_view.set_layer_visible(layer, visible)
            )
            if name == "point_cloud" and hasattr(bridge, "set_cloud_enabled"):
                checkbox.toggled.connect(bridge.set_cloud_enabled)
            self.layer_checks[name] = checkbox
            layer_layout.addWidget(checkbox, index // 2, index % 2)
        side_layout.addWidget(layer_group)

        goal_group = QGroupBox("点选导航目标")
        goal_layout = QGridLayout(goal_group)
        self.select_goal_button = QPushButton("在地图上选点")
        self.select_goal_button.setCheckable(True)
        self.select_goal_button.toggled.connect(self.map_view.set_goal_selection_enabled)
        self.goal_label = QLabel("尚未选点")
        self.goal_yaw = QDoubleSpinBox()
        self.goal_yaw.setRange(-180.0, 180.0)
        self.goal_yaw.setSuffix(" °")
        self.goal_yaw.setValue(0.0)
        self.send_goal_button = QPushButton("确认并发送目标")
        self.send_goal_button.setEnabled(False)
        self.send_goal_button.clicked.connect(self._send_goal)
        goal_layout.addWidget(self.select_goal_button, 0, 0, 1, 2)
        goal_layout.addWidget(self.goal_label, 1, 0, 1, 2)
        goal_layout.addWidget(QLabel("目标朝向"), 2, 0)
        goal_layout.addWidget(self.goal_yaw, 2, 1)
        goal_layout.addWidget(self.send_goal_button, 3, 0, 1, 2)
        self.map_view.goal_selected.connect(self._goal_selected)
        side_layout.addWidget(goal_group)

        self.teleop = TeleopPad()
        self.teleop.command_requested.connect(bridge.send_teleop)
        side_layout.addWidget(self.teleop)

        self.advanced_group = QGroupBox("高级参数")
        advanced_layout = QGridLayout(self.advanced_group)
        self.linear_speed = QDoubleSpinBox()
        self.linear_speed.setRange(0.02, 0.18)
        self.linear_speed.setSingleStep(0.01)
        self.linear_speed.setValue(0.12)
        self.linear_speed.setSuffix(" m/s")
        self.angular_speed = QDoubleSpinBox()
        self.angular_speed.setRange(0.10, 0.60)
        self.angular_speed.setSingleStep(0.05)
        self.angular_speed.setValue(0.45)
        self.angular_speed.setSuffix(" rad/s")
        self.linear_speed.valueChanged.connect(self._update_teleop_speeds)
        self.angular_speed.valueChanged.connect(self._update_teleop_speeds)
        advanced_layout.addWidget(QLabel("建图遥控线速度"), 0, 0)
        advanced_layout.addWidget(self.linear_speed, 0, 1)
        advanced_layout.addWidget(QLabel("建图遥控角速度"), 1, 0)
        advanced_layout.addWidget(self.angular_speed, 1, 1)
        note = QLabel("GUI 发送 20 Hz 租约；Orin agent 负责限幅、模式门控和 250 ms 超时停车。")
        note.setWordWrap(True)
        advanced_layout.addWidget(note, 2, 0, 1, 2)
        self.advanced_group.setVisible(False)
        side_layout.addWidget(self.advanced_group)
        side_layout.addStretch()
        splitter.addWidget(side)
        splitter.setStretchFactor(0, 1)
        root.addWidget(splitter, 1)

        status_group = QGroupBox("节点与服务状态")
        status_layout = QVBoxLayout(status_group)
        self.status_table = QTableWidget(0, 4)
        self.status_table.setHorizontalHeaderLabels(["模块", "状态", "就绪", "说明"])
        self.status_table.horizontalHeader().setStretchLastSection(True)
        self.status_table.verticalHeader().setVisible(False)
        self.status_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.status_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.status_table.setMaximumHeight(190)
        status_layout.addWidget(self.status_table)
        root.addWidget(status_group)

    def set_advanced(self, enabled: bool) -> None:
        self._advanced = bool(enabled)
        self.advanced_group.setVisible(self._advanced)

    def update_system_state(self, state: dict) -> None:
        self._mode = int(state.get("mode", self.MODE_OFF))
        names = {
            self.MODE_OFF: "系统关闭",
            self.MODE_MAPPING: "建图模式",
            self.MODE_NAVIGATION: "Navigation2",
            self.MODE_RF2O: "rf2o 回退",
            4: "切换中",
            5: "故障",
        }
        self.mode_badge.setText(names.get(self._mode, state.get("mode_name", "未知")))
        self.teleop.set_mapping_mode(self._connected and self._mode == self.MODE_MAPPING)
        can_goal = (
            self._connected
            and self._mode == self.MODE_NAVIGATION
            and self._pending_goal is not None
        )
        self.send_goal_button.setEnabled(can_goal)
        subsystems = list(state.get("subsystems", []))
        self.status_table.setRowCount(len(subsystems))
        for row, subsystem in enumerate(subsystems):
            values = [
                subsystem.get("display_name") or subsystem.get("name", ""),
                f"{subsystem.get('active_state', '')}/{subsystem.get('sub_state', '')}",
                "是" if subsystem.get("ready") else "否",
                subsystem.get("message", ""),
            ]
            for column, value in enumerate(values):
                self.status_table.setItem(row, column, QTableWidgetItem(str(value)))

    def set_connection_available(self, connected: bool) -> None:
        self._connected = bool(connected)
        self.teleop.set_mapping_mode(self._connected and self._mode == self.MODE_MAPPING)
        for button in (
            self.mapping_button,
            self.navigation_button,
            self.mapping_only_button,
            self.stop_button,
        ):
            button.setEnabled(self._connected)
        self.send_goal_button.setEnabled(
            self._connected
            and self._mode == self.MODE_NAVIGATION
            and self._pending_goal is not None
        )

    def update_grid(self, name: str, grid: GridLayer) -> None:
        self.map_view.set_grid(name, grid)

    def update_scan(self, points: list[tuple[float, float]]) -> None:
        self.map_view.set_scan(points)

    def update_cloud(self, points: list[tuple[float, float]]) -> None:
        self.map_view.set_cloud(points)

    def update_path(self, points: list[tuple[float, float]]) -> None:
        self.map_view.set_path(points)

    def update_robot(self, pose: Pose2D) -> None:
        self.map_view.set_robot(pose)

    def _goal_selected(self, x: float, y: float) -> None:
        self._pending_goal = Pose2D(x, y, math.radians(self.goal_yaw.value()))
        self.map_view.set_goal(self._pending_goal)
        self.goal_label.setText(f"x={x:.2f} m，y={y:.2f} m")
        self.select_goal_button.setChecked(False)
        self.send_goal_button.setEnabled(
            self._connected and self._mode == self.MODE_NAVIGATION
        )

    def _send_goal(self) -> None:
        if not self._connected or self._pending_goal is None or self._mode != self.MODE_NAVIGATION:
            return
        self._pending_goal = Pose2D(
            self._pending_goal.x,
            self._pending_goal.y,
            math.radians(self.goal_yaw.value()),
        )
        self.map_view.set_goal(self._pending_goal)
        answer = QMessageBox.question(
            self,
            "确认导航",
            f"确认让机器人导航到 x={self._pending_goal.x:.2f}, y={self._pending_goal.y:.2f}？",
        )
        if answer == QMessageBox.Yes:
            self._bridge.send_navigation_goal(self._pending_goal)

    def _update_teleop_speeds(self) -> None:
        self.teleop.set_speeds(self.linear_speed.value(), self.angular_speed.value())
