"""Operator page for fall detection, visual verification and Nav2 Spin status."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QAbstractItemView,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


STATUS_TEXT = {
    "accepted": "已接收",
    "scanning": "正在扫描",
    "verifying": "正在复核",
    "notified": "已通知联系人",
    "not_fall": "未确认跌倒",
    "cancelled": "已取消",
    "failed": "处理失败",
}


class FallResponsePage(QWidget):
    def __init__(self, bridge, config_client, parent=None) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._config = config_client
        self._advanced = False
        self._last_event_id = ""
        self._status_labels: dict[str, QLabel] = {}
        self._parameter_widgets = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        header = QHBoxLayout()
        title = QLabel("跌倒检测与紧急响应")
        title.setObjectName("pageTitle")
        self.mode_badge = QLabel("等待 Orin 跌倒检测服务")
        self.mode_badge.setObjectName("modeBadge")
        self.start_button = QPushButton("启动守护服务")
        self.stop_button = QPushButton("停止服务")
        self.restart_button = QPushButton("重启并读取配置")
        self.start_button.clicked.connect(bridge.start_fall_response)
        self.stop_button.clicked.connect(bridge.stop_fall_response)
        self.restart_button.clicked.connect(bridge.restart_fall_response)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.mode_badge)
        header.addWidget(self.start_button)
        header.addWidget(self.stop_button)
        header.addWidget(self.restart_button)
        root.addLayout(header)

        safety = QLabel(
            "服务启动本身不会启动 Nav2 或产生运动。真实旋转仅在 scan_mode=nav2_spin、"
            "收到真实事件且全部预检通过后由 Nav2 /spin 执行；中控不会发布 /cmd_vel。"
        )
        safety.setWordWrap(True)
        safety.setStyleSheet("color: #d2ad72;")
        root.addWidget(safety)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(4, 4, 4, 4)

        top = QHBoxLayout()
        camera_group = QGroupBox("车头实时画面与最近证据")
        camera_layout = QGridLayout(camera_group)
        self.camera_view = self._image_label("等待车头相机画面")
        self.evidence_view = self._image_label("候选/复查证据将在这里显示")
        camera_layout.addWidget(QLabel("实时画面"), 0, 0)
        camera_layout.addWidget(QLabel("最近证据"), 0, 1)
        camera_layout.addWidget(self.camera_view, 1, 0)
        camera_layout.addWidget(self.evidence_view, 1, 1)
        top.addWidget(camera_group, 3)

        status_group = QGroupBox("处置状态")
        status_layout = QVBoxLayout(status_group)
        self.event_label = QLabel("当前没有活动事件")
        self.event_label.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setRange(0, 12)
        self.progress.setValue(0)
        self.progress.setFormat("扫描 %v / %m")
        status_layout.addWidget(self.event_label)
        status_layout.addWidget(self.progress)
        grid = QGridLayout()
        fields = [
            ("camera_ready", "车头相机"),
            ("specialized_model_ready", "专用跌倒模型"),
            ("world_model_ready", "YOLO-World"),
            ("vlm_ready", "VLM 配置"),
            ("notification_ready", "微信联系人"),
            ("nav2_action_ready", "Nav2 Spin"),
            ("tf_ready", "TF"),
            ("odom_ready", "里程计"),
            ("costmap_ready", "局部代价地图"),
            ("rotation_clear", "旋转空间"),
            ("cmd_vel_clear", "速度控制权"),
            ("arm_safe", "机械臂安全"),
        ]
        for index, (key, label) in enumerate(fields):
            title_label = QLabel(label)
            value = QLabel("未知")
            value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._status_labels[key] = value
            grid.addWidget(title_label, index // 2, (index % 2) * 2)
            grid.addWidget(value, index // 2, (index % 2) * 2 + 1)
        status_layout.addLayout(grid)
        controls = QGridLayout()
        self.preflight_button = QPushButton("只读运行 Nav2 预检")
        self.demo_button = QPushButton("发起完整演示事件")
        self.cancel_button = QPushButton("取消当前处置并停车")
        self.cancel_button.setObjectName("dangerButton")
        self.wechat_button = QPushButton("重启微信通知")
        self.refresh_button = QPushButton("刷新事件")
        self.preflight_button.clicked.connect(bridge.run_fall_preflight)
        self.demo_button.clicked.connect(self._confirm_demo_event)
        self.cancel_button.clicked.connect(bridge.cancel_fall_response)
        self.wechat_button.clicked.connect(bridge.restart_wechatbot)
        self.refresh_button.clicked.connect(lambda: bridge.request_fall_events(30))
        controls.addWidget(self.preflight_button, 0, 0)
        controls.addWidget(self.cancel_button, 0, 1)
        controls.addWidget(self.demo_button, 1, 0)
        controls.addWidget(self.refresh_button, 1, 1)
        controls.addWidget(self.wechat_button, 2, 0, 1, 2)
        status_layout.addLayout(controls)
        self.status_message = QLabel("等待状态")
        self.status_message.setWordWrap(True)
        status_layout.addWidget(self.status_message)
        top.addWidget(status_group, 2)
        layout.addLayout(top)

        events_group = QGroupBox("事件与处置时间线")
        events_layout = QHBoxLayout(events_group)
        self.events = QTableWidget(0, 5)
        self.events.setHorizontalHeaderLabels(
            ["接收时间", "来源", "状态", "本地/VLM", "事件 ID"]
        )
        self.events.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents
        )
        self.events.horizontalHeader().setStretchLastSection(True)
        self.events.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.events.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.events.cellClicked.connect(self._event_selected)
        self.timeline = QTableWidget(0, 3)
        self.timeline.setHorizontalHeaderLabels(["时间", "阶段", "说明"])
        self.timeline.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents
        )
        self.timeline.horizontalHeader().setStretchLastSection(True)
        self.timeline.setEditTriggers(QAbstractItemView.NoEditTriggers)
        events_layout.addWidget(self.events, 3)
        events_layout.addWidget(self.timeline, 2)
        layout.addWidget(events_group)

        config_group = QGroupBox("跌倒检测配置")
        config_layout = QVBoxLayout(config_group)
        simple_form = QFormLayout()
        self.scan_mode = QComboBox()
        self.scan_mode.addItem("静态无运动（安全回退）", "static")
        self.scan_mode.addItem("真实 Nav2 分段旋转", "nav2_spin")
        self.notification_enabled = QCheckBox("允许向已绑定的真实联系人发送通知")
        self.scan_steps = self._spin(4, 36)
        self.frames_per_angle = self._spin(1, 8)
        self.clearance = self._double(0.30, 1.50, 0.01, " m")
        simple_form.addRow("扫描方式", self.scan_mode)
        simple_form.addRow("真实联系人通知", self.notification_enabled)
        simple_form.addRow("一圈分段数量", self.scan_steps)
        simple_form.addRow("每个方向拍摄帧数", self.frames_per_angle)
        simple_form.addRow("旋转安全半径", self.clearance)
        config_layout.addLayout(simple_form)

        self.advanced_group = QGroupBox("高级检测、复查与 Nav2 参数")
        advanced_form = QFormLayout(self.advanced_group)
        specs = [
            ("recheck_frames", "候选复查帧数", self._spin(1, 6)),
            ("frame_interval_sec", "连续拍摄间隔", self._double(0.0, 2.0, 0.01, " s")),
            ("strong_fallen_threshold", "强跌倒候选阈值", self._double(0.05, 1.0, 0.01)),
            ("weak_fallen_threshold", "弱跌倒候选阈值", self._double(0.01, 1.0, 0.01)),
            ("recheck_frame_threshold", "复查单帧阈值", self._double(0.05, 1.0, 0.01)),
            ("recheck_average_threshold", "复查平均阈值", self._double(0.05, 1.0, 0.01)),
            ("world_person_threshold", "人体兜底阈值", self._double(0.05, 1.0, 0.01)),
            ("vlm_threshold", "VLM 确认阈值", self._double(0.05, 1.0, 0.01)),
            ("rotation_obstacle_cost_threshold", "障碍代价阈值", self._spin(1, 100)),
            ("stop_angular_velocity_rps", "停车角速度阈值", self._double(0.005, 0.2, 0.005, " rad/s")),
            ("stop_stable_sec", "稳定停车持续时间", self._double(0.05, 2.0, 0.05, " s")),
            ("spin_timeout_sec", "单段 Spin 超时", self._double(2.0, 60.0, 0.5, " s")),
            ("costmap_ttl_sec", "代价地图最大延迟", self._double(0.1, 10.0, 0.1, " s")),
        ]
        for key, label, widget in specs:
            widget.setToolTip(f"Orin 参数：{key}")
            self._parameter_widgets[key] = widget
            advanced_form.addRow(label, widget)
        self.require_arm_torque_off = QCheckBox("旋转前要求机械臂关闭扭矩")
        self.cancel_competing = QCheckBox("旋转前取消已有 Nav2 导航目标")
        self._parameter_widgets["require_arm_torque_off"] = self.require_arm_torque_off
        self._parameter_widgets["cancel_competing_actions"] = self.cancel_competing
        advanced_form.addRow("机械臂门槛", self.require_arm_torque_off)
        advanced_form.addRow("导航互斥", self.cancel_competing)
        self.advanced_group.setVisible(False)
        config_layout.addWidget(self.advanced_group)
        buttons = QHBoxLayout()
        self.load_button = QPushButton("读取 Orin 配置")
        self.save_button = QPushButton("保存配置")
        self.save_button.setEnabled(False)
        self.load_button.clicked.connect(lambda: config_client.load("fall"))
        self.save_button.clicked.connect(self._save_config)
        buttons.addStretch()
        buttons.addWidget(self.load_button)
        buttons.addWidget(self.save_button)
        config_layout.addLayout(buttons)
        layout.addWidget(config_group)
        layout.addStretch()
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        bridge.fall_status.connect(self.update_status)
        bridge.fall_events.connect(self.update_events)
        bridge.fall_event_detail.connect(self.update_event_detail)
        bridge.fall_operation.connect(self._operation)
        bridge.fall_control_available.connect(self._control_available)
        config_client.loaded.connect(self._config_loaded)
        config_client.saved.connect(self._config_saved)
        config_client.failed.connect(self._config_failed)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(5000)
        self._refresh_timer.timeout.connect(lambda: bridge.request_fall_events(30))
        self._refresh_timer.start()
        QTimer.singleShot(0, lambda: bridge.request_fall_events(30))

    @staticmethod
    def _image_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setAlignment(Qt.AlignCenter)
        label.setMinimumSize(280, 158)
        label.setStyleSheet("background: #101318; border: 1px solid #303741;")
        label.setScaledContents(False)
        return label

    @staticmethod
    def _spin(minimum: int, maximum: int) -> QSpinBox:
        widget = QSpinBox()
        widget.setRange(minimum, maximum)
        return widget

    @staticmethod
    def _double(minimum: float, maximum: float, step: float, suffix: str = "") -> QDoubleSpinBox:
        widget = QDoubleSpinBox()
        widget.setRange(minimum, maximum)
        widget.setSingleStep(step)
        widget.setDecimals(3)
        widget.setSuffix(suffix)
        return widget

    @staticmethod
    def _time(ms: int) -> str:
        if not ms:
            return "—"
        return datetime.fromtimestamp(ms / 1000.0).strftime("%m-%d %H:%M:%S")

    @staticmethod
    def _set_image(label: QLabel, jpeg_data: bytes) -> None:
        pixmap = QPixmap()
        if not pixmap.loadFromData(jpeg_data):
            label.setText("图像解码失败")
            return
        label.setPixmap(
            pixmap.scaled(
                label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
        )

    def update_camera(self, jpeg_data: bytes) -> None:
        self._set_image(self.camera_view, jpeg_data)

    def update_evidence(self, jpeg_data: bytes) -> None:
        self._set_image(self.evidence_view, jpeg_data)

    def update_status(self, status: dict) -> None:
        mode = status.get("scan_mode", "static")
        self.mode_badge.setText(
            "真实 Nav2 分段旋转" if mode == "nav2_spin" else "静态无运动模式"
        )
        nav_only = {
            "nav2_action_ready",
            "tf_ready",
            "odom_ready",
            "costmap_ready",
            "rotation_clear",
            "cmd_vel_clear",
            "arm_safe",
        }
        for key, label in self._status_labels.items():
            if mode == "static" and key in nav_only:
                label.setText("静态模式不需要")
                label.setStyleSheet("color: #8e98a5;")
                continue
            ready = bool(status.get(key, False))
            label.setText("就绪" if ready else "未就绪")
            label.setStyleSheet("color: #81c784;" if ready else "color: #ef9a9a;")
        total = max(1, int(status.get("scan_total", 12)))
        self.progress.setRange(0, total)
        self.progress.setValue(min(total, int(status.get("scan_step", 0))))
        event_id = str(status.get("active_event_id", ""))
        if status.get("event_active"):
            heading = (
                f"\n方向：{status.get('current_heading_deg', 0):.1f}° → "
                f"{status.get('target_heading_deg', 0):.1f}°"
                if mode == "nav2_spin"
                else ""
            )
            self.event_label.setText(
                f"事件 {event_id}\n阶段：{status.get('stage', '')}{heading}\n"
                f"本地置信度 {status.get('local_confidence', 0):.2f} / "
                f"VLM {status.get('vlm_confidence', 0):.2f}"
            )
            if event_id and event_id != self._last_event_id:
                self._last_event_id = event_id
                self._bridge.request_fall_event(event_id)
        else:
            self.event_label.setText("当前没有活动事件")
        self.cancel_button.setEnabled(bool(status.get("event_active")))
        self.status_message.setText(str(status.get("message", "")))

    def update_system_state(self, state: dict) -> None:
        units = {
            item.get("name", ""): bool(item.get("ready"))
            for item in state.get("subsystems", [])
        }
        fall_ready = units.get("project-link-fall-response.service", False)
        wechat_ready = units.get("project-link-wechatbot.service", False)
        if not fall_ready:
            self.mode_badge.setText("跌倒检测服务未启动")
        if "notification_ready" in self._status_labels and not wechat_ready:
            label = self._status_labels["notification_ready"]
            label.setText("服务未启动")
            label.setStyleSheet("color: #ef9a9a;")

    def update_events(self, events: list) -> None:
        self.events.setRowCount(len(events))
        for row, event in enumerate(events):
            confidence = (
                f"{event.get('local_confidence', 0):.2f} / "
                f"{event.get('vlm_confidence', 0):.2f}"
            )
            values = [
                self._time(int(event.get("received_at_ms", 0))),
                str(event.get("device_name", "")),
                STATUS_TEXT.get(event.get("status"), str(event.get("status", ""))),
                confidence,
                str(event.get("event_id", "")),
            ]
            for column, value in enumerate(values):
                self.events.setItem(row, column, QTableWidgetItem(value))

    def _event_selected(self, row: int, _column: int) -> None:
        item = self.events.item(row, 4)
        if item is not None:
            self._bridge.request_fall_event(item.text())

    def update_event_detail(self, detail: dict) -> None:
        transitions = list(detail.get("transitions", []))
        self.timeline.setRowCount(len(transitions))
        for row, transition in enumerate(transitions):
            values = [
                self._time(int(transition.get("created_at_ms", 0))),
                str(transition.get("stage", "")),
                str(transition.get("message", "")),
            ]
            for column, value in enumerate(values):
                self.timeline.setItem(row, column, QTableWidgetItem(value))

    def _control_available(self, ready: bool, message: str) -> None:
        self.start_button.setEnabled(ready)
        self.stop_button.setEnabled(ready)
        self.restart_button.setEnabled(ready)
        if not ready:
            self.mode_badge.setText(message)

    def _operation(self, message: str) -> None:
        self.status_message.setText(message)
        self._bridge.request_fall_events(30)

    def _confirm_demo_event(self) -> None:
        answer = QMessageBox.warning(
            self,
            "发起完整跌倒演示事件",
            "这会走完整的相机、模型、VLM 和通知链路。若当前配置为真实 Nav2，"
            "机器人可能原地旋转；若真实联系人通知已开启，联系人可能收到消息。\n\n"
            "请先清空旋转区域、收拢机械臂并准备物理急停。确认继续吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self._bridge.create_fall_demo_event()

    def _config_loaded(self, section: str, data: dict) -> None:
        if section != "fall":
            return
        index = self.scan_mode.findData(str(data.get("scan_mode", "static")))
        self.scan_mode.setCurrentIndex(max(0, index))
        parameters = data.get("parameters", {})
        self.notification_enabled.setChecked(bool(parameters.get("notification_enabled", True)))
        self.scan_steps.setValue(int(parameters.get("simulated_scan_steps", 12)))
        self.frames_per_angle.setValue(int(parameters.get("frames_per_angle", 3)))
        self.clearance.setValue(float(parameters.get("rotation_clearance_radius_m", 0.42)))
        for key, widget in self._parameter_widgets.items():
            value = parameters.get(key)
            if value is None:
                continue
            if isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            elif isinstance(widget, QSpinBox):
                widget.setValue(int(value))
            else:
                widget.setValue(float(value))
        self.save_button.setEnabled(True)
        self.status_message.setText("已读取 Orin 跌倒检测配置")

    def _save_config(self) -> None:
        parameters = {
            "notification_enabled": self.notification_enabled.isChecked(),
            "simulated_scan_steps": self.scan_steps.value(),
            "frames_per_angle": self.frames_per_angle.value(),
            "rotation_clearance_radius_m": self.clearance.value(),
        }
        for key, widget in self._parameter_widgets.items():
            parameters[key] = (
                widget.isChecked()
                if isinstance(widget, QCheckBox)
                else widget.value()
            )
        self._config.save(
            "fall",
            {"scan_mode": self.scan_mode.currentData(), "parameters": parameters},
        )
        self.status_message.setText("正在保存配置…")

    def _config_saved(self, section: str, _data: dict) -> None:
        if section == "fall":
            self.status_message.setText("配置已保存；点击“重启并读取配置”后生效")

    def _config_failed(self, section: str, message: str) -> None:
        if section == "fall":
            self.status_message.setText("配置读取/保存失败：" + message)

    def set_advanced(self, enabled: bool) -> None:
        self._advanced = bool(enabled)
        self.advanced_group.setVisible(self._advanced)
        self.wechat_button.setVisible(self._advanced)
