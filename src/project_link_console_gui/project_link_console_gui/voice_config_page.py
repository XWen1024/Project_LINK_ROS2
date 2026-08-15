"""Editable voice parameters, prompt profiles, and safe registered tools."""

from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


REGISTERED_TOOLS = {
    "get_weather": "查询指定城市的实时天气",
    "get_current_location": "读取机器人当前地图位置",
    "save_waypoint": "保存当前地图位置为命名航点",
    "list_saved_locations": "列出已经保存的命名航点",
    "navigate_to_location": "创建需要确认的命名航点导航任务",
    "fetch_item_from_location": "创建需要确认的导航与抓取任务",
    "cancel_current_task": "取消当前待确认或正在执行的任务",
}


PARAMETERS = [
    ("classic", "audio_end_silence_ms", "语音结束静音", "ms", 100, 3000, 50, True),
    ("classic", "audio_no_speech_timeout_sec", "无语音超时", "s", 1.0, 60.0, 0.5, True),
    ("classic", "audio_max_utterance_sec", "单次最长讲话", "s", 2.0, 60.0, 0.5, True),
    ("classic", "continuous_silence_timeout_sec", "连续对话静默退出", "s", 2.0, 120.0, 0.5, True),
    ("classic", "audio_pre_roll_ms", "VAD 前置保留", "ms", 0, 2000, 50, False),
    ("classic", "audio_min_speech_sec", "最短有效语音", "s", 0.05, 3.0, 0.05, False),
    ("classic", "volcano_asr_packet_ms", "ASR 发包间隔", "ms", 20, 1000, 20, False),
    ("classic", "volcano_asr_final_timeout_sec", "ASR 最终结果超时", "s", 0.5, 15.0, 0.5, False),
    ("classic", "waiting_prompt_delay_ms", "等待提示延迟", "ms", 0, 3000, 50, False),
    ("classic", "confirmation_timeout_sec", "动作确认超时", "s", 5.0, 120.0, 1.0, False),
    ("qwen", "turn_detection_threshold", "语义 VAD 阈值", "", 0.0, 1.0, 0.05, True),
    ("qwen", "turn_detection_silence_duration_ms", "语义 VAD 静音", "ms", 100, 5000, 100, True),
    ("qwen", "prefix_padding_ms", "语音前缀保留", "ms", 0, 2000, 50, True),
    ("qwen", "continuous_silence_timeout_sec", "连续对话静默退出", "s", 2.0, 180.0, 1.0, True),
    ("qwen", "barge_in_enabled", "允许说话打断播报", "", False, True, None, True),
    ("qwen", "audio_input_chunk_ms", "麦克风分块", "ms", 20, 500, 10, False),
    ("qwen", "audio_output_chunk_ms", "扬声器分块", "ms", 10, 500, 10, False),
    ("qwen", "first_turn_no_speech_timeout_sec", "首轮无语音超时", "s", 2.0, 60.0, 0.5, False),
    ("qwen", "confirmation_timeout_sec", "动作确认超时", "s", 5.0, 120.0, 1.0, False),
]


class VoiceConfigPage(QWidget):
    def __init__(self, config_client, parent=None) -> None:
        super().__init__(parent)
        self._client = config_client
        self._widgets: dict[tuple[str, str], QWidget] = {}
        self._advanced_widgets: list[QWidget] = []
        self._loaded = False

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        header = QHBoxLayout()
        title = QLabel("语音配置")
        title.setObjectName("pageTitle")
        self.state_label = QLabel("尚未读取 Orin 配置")
        self.state_label.setObjectName("modeBadge")
        self.load_button = QPushButton("从 Orin 读取")
        self.save_button = QPushButton("保存并等待下次重启生效")
        self.save_button.setEnabled(False)
        self.load_button.clicked.connect(lambda: self._client.load("voice"))
        self.save_button.clicked.connect(self._save)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.state_label)
        header.addWidget(self.load_button)
        header.addWidget(self.save_button)
        root.addLayout(header)

        tabs = QTabWidget()
        tabs.addTab(self._parameter_tab(), "常用参数")
        tabs.addTab(self._prompt_tab(), "系统提示词")
        tabs.addTab(self._tool_tab(), "工具注册表")
        root.addWidget(tabs, 1)

        self._client.loaded.connect(self._on_loaded)
        self._client.saved.connect(self._on_saved)
        self._client.failed.connect(self._on_failed)

    def _parameter_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(12, 12, 12, 12)
        for backend, title in (("classic", "经典链路"), ("qwen", "Qwen Realtime")):
            box = QGroupBox(title)
            form = QFormLayout(box)
            for item in PARAMETERS:
                item_backend, key, label, unit, minimum, maximum, step, simple = item
                if item_backend != backend:
                    continue
                widget = self._parameter_widget(minimum, maximum, step, unit)
                widget.setToolTip(f"原始参数：{key}")
                self._widgets[(backend, key)] = widget
                row_label = QLabel(label + (f"（{unit}）" if unit else ""))
                form.addRow(row_label, widget)
                if not simple:
                    row_label.setVisible(False)
                    widget.setVisible(False)
                    self._advanced_widgets.extend([row_label, widget])
            layout.addWidget(box)
        note = QLabel("这些配置写入 Orin 本地覆盖文件，不会自动启动或重启语音服务。")
        note.setWordWrap(True)
        note.setStyleSheet("color: #8f99a6;")
        layout.addWidget(note)
        layout.addStretch()
        scroll.setWidget(body)
        return scroll

    @staticmethod
    def _parameter_widget(minimum, maximum, step, unit: str) -> QWidget:
        if isinstance(minimum, bool):
            return QCheckBox("启用")
        if unit == "ms" and isinstance(minimum, int):
            widget = QSpinBox()
            widget.setRange(int(minimum), int(maximum))
            widget.setSingleStep(int(step))
            return widget
        widget = QDoubleSpinBox()
        widget.setRange(float(minimum), float(maximum))
        widget.setSingleStep(float(step))
        widget.setDecimals(2)
        return widget

    def _prompt_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        note = QLabel("提示词只决定模型表达与工具选择；Python 安全层、确认门禁和 ROS 执行权限不受提示词修改。")
        note.setWordWrap(True)
        note.setStyleSheet("color: #d2ad72;")
        layout.addWidget(note)
        classic_box = QGroupBox("经典链路系统提示词")
        classic_layout = QVBoxLayout(classic_box)
        self.classic_prompt = QPlainTextEdit()
        self.classic_prompt.setPlaceholderText("经典链路系统提示词")
        classic_layout.addWidget(self.classic_prompt)
        qwen_box = QGroupBox("Qwen Realtime 系统提示词")
        qwen_layout = QVBoxLayout(qwen_box)
        self.qwen_prompt = QPlainTextEdit()
        self.qwen_prompt.setPlaceholderText("Qwen Realtime instructions")
        qwen_layout.addWidget(self.qwen_prompt)
        layout.addWidget(classic_box, 1)
        layout.addWidget(qwen_box, 1)
        return page

    def _tool_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        note = QLabel("只能添加仓库内已注册、由 Python 执行器实现的工具；不能在 GUI 中填写任意命令或 ROS 接口。")
        note.setWordWrap(True)
        note.setStyleSheet("color: #d2ad72;")
        layout.addWidget(note)
        self.tools = QTableWidget(0, 4)
        self.tools.setHorizontalHeaderLabels(["启用", "工具名称", "中文说明", "执行器"])
        self.tools.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tools.setAlternatingRowColors(True)
        self.tools.verticalHeader().setVisible(False)
        self.tools.horizontalHeader().setStretchLastSection(True)
        self.tools.setColumnWidth(0, 60)
        self.tools.setColumnWidth(1, 210)
        self.tools.setColumnWidth(2, 480)
        self.tools.itemSelectionChanged.connect(self._show_schema)
        layout.addWidget(self.tools, 1)
        row = QHBoxLayout()
        self.tool_picker = QComboBox()
        for name, description in REGISTERED_TOOLS.items():
            self.tool_picker.addItem(f"{name} — {description}", name)
        add_button = QPushButton("添加已注册工具")
        remove_button = QPushButton("从配置中删除")
        add_button.clicked.connect(self._add_selected_tool)
        remove_button.clicked.connect(self._remove_selected_tool)
        row.addWidget(self.tool_picker, 1)
        row.addWidget(add_button)
        row.addWidget(remove_button)
        layout.addLayout(row)
        self.schema_box = QGroupBox("参数 Schema（高级模式）")
        schema_layout = QVBoxLayout(self.schema_box)
        self.schema_editor = QPlainTextEdit()
        self.schema_editor.setPlaceholderText("选中工具后显示 JSON Schema")
        self.schema_editor.textChanged.connect(self._store_schema)
        schema_layout.addWidget(self.schema_editor)
        self.schema_box.setVisible(False)
        layout.addWidget(self.schema_box)
        return page

    def _add_selected_tool(self) -> None:
        name = str(self.tool_picker.currentData() or "")
        if not name:
            return
        for row in range(self.tools.rowCount()):
            if self.tools.item(row, 1).text() == name:
                self.tools.selectRow(row)
                return
        self._append_tool(
            {
                "name": name,
                "enabled": True,
                "description": REGISTERED_TOOLS[name],
                "parameters": {"type": "object", "properties": {}, "required": []},
            }
        )

    def _append_tool(self, tool: dict) -> None:
        row = self.tools.rowCount()
        self.tools.insertRow(row)
        enabled = QTableWidgetItem()
        enabled.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
        enabled.setCheckState(Qt.Checked if tool.get("enabled", True) else Qt.Unchecked)
        name = str(tool.get("name", ""))
        name_item = QTableWidgetItem(name)
        name_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        name_item.setData(Qt.UserRole, tool.get("parameters", {}))
        self.tools.setItem(row, 0, enabled)
        self.tools.setItem(row, 1, name_item)
        self.tools.setItem(row, 2, QTableWidgetItem(str(tool.get("description", ""))))
        self.tools.setItem(row, 3, QTableWidgetItem("已绑定" if name in REGISTERED_TOOLS else "不可用"))

    def _remove_selected_tool(self) -> None:
        row = self.tools.currentRow()
        if row >= 0:
            self.tools.removeRow(row)

    def _show_schema(self) -> None:
        row = self.tools.currentRow()
        self.schema_editor.blockSignals(True)
        if row < 0:
            self.schema_editor.clear()
        else:
            value = self.tools.item(row, 1).data(Qt.UserRole) or {}
            self.schema_editor.setPlainText(json.dumps(value, ensure_ascii=False, indent=2))
        self.schema_editor.blockSignals(False)

    def _store_schema(self) -> None:
        row = self.tools.currentRow()
        if row < 0:
            return
        try:
            value = json.loads(self.schema_editor.toPlainText() or "{}")
        except json.JSONDecodeError:
            self.schema_editor.setStyleSheet("border: 1px solid #b95c5c;")
            return
        self.schema_editor.setStyleSheet("")
        self.tools.item(row, 1).setData(Qt.UserRole, value)

    def _on_loaded(self, section: str, data: dict) -> None:
        if section != "voice":
            return
        for (backend, key), widget in self._widgets.items():
            value = data.get(backend, {}).get(key)
            if value is None:
                continue
            if isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            elif isinstance(widget, QSpinBox):
                widget.setValue(int(value))
            elif isinstance(widget, QDoubleSpinBox):
                widget.setValue(float(value))
        profile = data.get("profile", {})
        prompts = profile.get("prompts", {})
        self.classic_prompt.setPlainText(str(prompts.get("classic", "")))
        self.qwen_prompt.setPlainText(str(prompts.get("qwen_realtime", "")))
        self.tools.setRowCount(0)
        for tool in profile.get("tools", []):
            self._append_tool(tool)
        self._loaded = True
        self.save_button.setEnabled(True)
        self.state_label.setText("已读取")

    def _save(self) -> None:
        if not self._loaded:
            return
        payload: dict[str, dict] = {"classic": {}, "qwen": {}}
        for (backend, key), widget in self._widgets.items():
            if isinstance(widget, QCheckBox):
                value = widget.isChecked()
            elif isinstance(widget, QSpinBox):
                value = widget.value()
            else:
                value = widget.value()
            payload[backend][key] = value
        tools = []
        for row in range(self.tools.rowCount()):
            tools.append(
                {
                    "enabled": self.tools.item(row, 0).checkState() == Qt.Checked,
                    "name": self.tools.item(row, 1).text().strip(),
                    "description": self.tools.item(row, 2).text().strip(),
                    "parameters": self.tools.item(row, 1).data(Qt.UserRole) or {},
                }
            )
        payload["profile"] = {
            "prompts": {
                "classic": self.classic_prompt.toPlainText().strip(),
                "qwen_realtime": self.qwen_prompt.toPlainText().strip(),
            },
            "tools": tools,
        }
        self.state_label.setText("正在保存…")
        self._client.save("voice", payload)

    def _on_saved(self, section: str, _data: dict) -> None:
        if section == "voice":
            self.state_label.setText("已保存，重启语音后生效")

    def _on_failed(self, section: str, message: str) -> None:
        if section == "voice":
            self.state_label.setText("读取/保存失败")
            self.state_label.setToolTip(message)

    def set_advanced(self, enabled: bool) -> None:
        for widget in self._advanced_widgets:
            widget.setVisible(bool(enabled))
        self.schema_box.setVisible(bool(enabled))
