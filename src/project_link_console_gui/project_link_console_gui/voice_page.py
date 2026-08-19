"""Voice backend control, state, and concise timing diagnostics."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


BACKEND_LABELS = {
    "off": "已关闭",
    "classic": "经典链路",
    "qwen_realtime": "Qwen Realtime",
    "qwen-realtime": "Qwen Realtime",
}

STATE_LABELS = {
    "unknown": "未知",
    "idle": "待命",
    "connecting": "正在连接模型",
    "waiting_wakeup": "等待唤醒",
    "等待唤醒": "等待唤醒",
    "已唤醒": "已唤醒",
    "唤醒串口异常": "唤醒串口异常",
    "正在连接唤醒串口": "正在连接唤醒串口",
    "conversation_active": "对话中",
    "listening": "正在聆听",
    "thinking": "正在处理",
    "speaking": "正在播报",
    "invalid": "状态异常",
}


class StatusCard(QFrame):
    def __init__(self, title: str, value: str = "-", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("statusCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        title_label = QLabel(title)
        title_label.setStyleSheet("color: #8f99a6; font-size: 12px;")
        self.value = QLabel(value)
        self.value.setStyleSheet("font-size: 17px; font-weight: 600;")
        self.value.setWordWrap(True)
        layout.addWidget(title_label)
        layout.addWidget(self.value)


class VoicePage(QWidget):
    def __init__(self, bridge, parent=None) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._connected = False
        self._control_available = False
        self._backend = "off"

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        header = QHBoxLayout()
        title = QLabel("语音控制")
        title.setObjectName("pageTitle")
        self.backend_badge = QLabel("已关闭")
        self.backend_badge.setObjectName("modeBadge")
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.backend_badge)
        root.addLayout(header)

        connection = QGroupBox("1. 连接 Orin 语音控制")
        connection_layout = QHBoxLayout(connection)
        connection_text = QVBoxLayout()
        self.control_connection = QLabel("正在自动发现 Orin 中控代理…")
        self.control_connection.setStyleSheet("font-weight: 600;")
        self.connection_hint = QLabel(
            "无需填写 IP。Ubuntu 与 Orin 需在同一局域网，并使用 ROS_DOMAIN_ID=42；"
            "检测成功后再选择下方语音后端。"
        )
        self.connection_hint.setWordWrap(True)
        self.connection_hint.setStyleSheet("color: #8f99a6;")
        connection_text.addWidget(self.control_connection)
        connection_text.addWidget(self.connection_hint)
        self.probe_button = QPushButton("重新检测连接")
        self.probe_button.clicked.connect(self._probe_connection)
        connection_layout.addLayout(connection_text, 1)
        connection_layout.addWidget(self.probe_button)
        root.addWidget(connection)

        controls = QGroupBox("2. 启动语音后端（互斥）")
        control_layout = QHBoxLayout(controls)
        self.classic_button = QPushButton("启动经典链路")
        self.qwen_button = QPushButton("启动 Qwen Realtime")
        self.stop_button = QPushButton("关闭语音服务")
        self.stop_button.setObjectName("dangerButton")
        self.classic_button.clicked.connect(lambda: self._request_switch(1, "经典链路"))
        self.qwen_button.clicked.connect(lambda: self._request_switch(2, "Qwen Realtime"))
        self.stop_button.clicked.connect(lambda: self._request_switch(0, "关闭语音服务"))
        control_layout.addWidget(self.classic_button)
        control_layout.addWidget(self.qwen_button)
        control_layout.addWidget(self.stop_button)
        control_layout.addStretch()
        self.operation_label = QLabel("等待操作")
        self.operation_label.setStyleSheet("color: #8f99a6;")
        self.operation_label.setWordWrap(True)
        control_layout.addWidget(self.operation_label, 1)
        root.addWidget(controls)

        cards = QGridLayout()
        self.service_card = StatusCard("服务状态", "未连接")
        self.wakeup_card = StatusCard("唤醒状态", "未知")
        self.session_card = StatusCard("会话状态", "未知")
        self.task_card = StatusCard("机器人任务", "无")
        cards.addWidget(self.service_card, 0, 0)
        cards.addWidget(self.wakeup_card, 0, 1)
        cards.addWidget(self.session_card, 0, 2)
        cards.addWidget(self.task_card, 0, 3)
        root.addLayout(cards)

        event_box = QGroupBox("实时阶段日志")
        event_layout = QVBoxLayout(event_box)
        hint = QLabel("同一条语音请求按时间顺序显示阶段耗时；Δ 为相对上一步，Σ 为累计耗时。")
        hint.setStyleSheet("color: #8f99a6;")
        event_layout.addWidget(hint)
        self.events = QTableWidget(0, 5)
        self.events.setHorizontalHeaderLabels(["时间", "阶段", "Δ 上一步", "Σ 累计", "说明"])
        self.events.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.events.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.events.setAlternatingRowColors(True)
        self.events.verticalHeader().setVisible(False)
        self.events.horizontalHeader().setStretchLastSection(True)
        self.events.setColumnWidth(0, 82)
        self.events.setColumnWidth(1, 190)
        self.events.setColumnWidth(2, 100)
        self.events.setColumnWidth(3, 100)
        event_layout.addWidget(self.events, 1)
        row = QHBoxLayout()
        self.clear_button = QPushButton("清空页面日志")
        self.clear_button.clicked.connect(lambda: self.events.setRowCount(0))
        row.addStretch()
        row.addWidget(self.clear_button)
        event_layout.addLayout(row)
        root.addWidget(event_box, 1)

        self.advanced_box = QGroupBox("高级诊断")
        advanced_layout = QVBoxLayout(self.advanced_box)
        self.raw_status = QLabel("尚未收到 /voice/status")
        self.raw_status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.raw_status.setWordWrap(True)
        advanced_layout.addWidget(self.raw_status)
        self.advanced_box.setVisible(False)
        root.addWidget(self.advanced_box)
        self._refresh_buttons()

    def set_connection_available(self, connected: bool) -> None:
        self._connected = bool(connected)
        if connected:
            self.set_voice_control_available(True, "Orin 状态心跳正常，语音控制可用")
        if not connected:
            self.service_card.value.setText("状态心跳未连接")
        self._refresh_buttons()

    def set_voice_control_available(self, available: bool, message: str) -> None:
        self._control_available = bool(available)
        color = "#81c784" if available else "#ef9a9a"
        prefix = "● " if available else "○ "
        self.control_connection.setText(prefix + message)
        self.control_connection.setStyleSheet(f"font-weight: 600; color: {color};")
        self._refresh_buttons()

    def _probe_connection(self) -> None:
        self.control_connection.setText("正在检测 Orin 语音控制 Action…")
        self.control_connection.setStyleSheet("font-weight: 600; color: #ffd180;")
        self._bridge.probe_voice_control()

    def _request_switch(self, backend: int, label: str) -> None:
        self.operation_label.setText(f"正在请求：{label}…")
        self._bridge.switch_voice(backend)

    def show_voice_operation(self, message: str) -> None:
        self.operation_label.setText(message)

    def update_system_state(self, state: dict) -> None:
        backend = str(state.get("voice_backend", "off") or "off")
        self._backend = backend
        self.backend_badge.setText(BACKEND_LABELS.get(backend, backend))
        voice_units = {
            item.get("name", ""): item
            for item in state.get("subsystems", [])
            if "voice" in str(item.get("name", ""))
        }
        active = [item for item in voice_units.values() if item.get("ready")]
        self.service_card.value.setText(
            "运行中" if active else ("已关闭" if self._connected else "未连接")
        )
        self._refresh_buttons()

    def update_voice_status(self, status: dict) -> None:
        backend = str(status.get("backend", self._backend) or self._backend)
        state = str(status.get("state", "unknown"))
        self._backend = backend
        self.backend_badge.setText(BACKEND_LABELS.get(backend, backend))
        self.service_card.value.setText("运行中")
        wake_state = str(status.get("wakeup_state", ""))
        if not wake_state:
            wake_state = "已唤醒" if status.get("conversation_active") else "等待唤醒"
        wake_text = STATE_LABELS.get(wake_state, wake_state)
        if not status.get("conversation_active") and status.get("wakeup_serial_state") == "ready":
            events = int(status.get("wakeup_events_seen", 0) or 0)
            bytes_seen = int(status.get("wakeup_bytes_seen", 0) or 0)
            if events:
                wake_text = f"等待唤醒 · 已识别 {events} 次"
            elif bytes_seen:
                wake_text = "收到串口数据，未识别唤醒事件"
            else:
                wake_text = "等待唤醒 · 串口已连接"
        self.wakeup_card.value.setText(wake_text)
        self.session_card.value.setText(STATE_LABELS.get(state, state))
        pending = str(status.get("pending_task", "") or "")
        active = str(status.get("active_task", "") or "")
        self.task_card.value.setText(active or pending or "无")
        self.raw_status.setText(str(status.get("raw", status)))
        self._refresh_buttons()

    def append_event(self, event: dict) -> None:
        if str(event.get("subsystem", "")) not in {"voice", "asr", "llm", "tts", "vad"}:
            return
        row = self.events.rowCount()
        self.events.insertRow(row)
        values = [
            datetime.now().strftime("%H:%M:%S"),
            str(event.get("phase", "") or event.get("subsystem", "voice")),
            self._milliseconds(event.get("delta_ms")),
            self._milliseconds(event.get("total_ms")),
            str(event.get("message", "")),
        ]
        for column, value in enumerate(values):
            self.events.setItem(row, column, QTableWidgetItem(value))
        if self.events.rowCount() > 300:
            self.events.removeRow(0)
        self.events.scrollToBottom()

    @staticmethod
    def _milliseconds(value) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return "-"
        return "-" if number <= 0.0 else f"{number:.0f} ms"

    def _refresh_buttons(self) -> None:
        enabled = self._control_available
        self.classic_button.setEnabled(enabled and self._backend != "classic")
        self.qwen_button.setEnabled(enabled and self._backend not in {"qwen_realtime", "qwen-realtime"})
        self.stop_button.setEnabled(enabled and self._backend != "off")

    def set_advanced(self, enabled: bool) -> None:
        self.advanced_box.setVisible(bool(enabled))
