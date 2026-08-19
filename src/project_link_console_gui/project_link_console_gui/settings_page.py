"""Unified local connection, Orin environment, device, and secret settings."""

from __future__ import annotations

import os

from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


FIELDS = [
    ("console", "PROJECT_LINK_WORKSPACE", "Orin 工作区", False, True),
    ("console", "ROS_DOMAIN_ID", "ROS Domain ID", False, True),
    ("console", "ROS_LOCALHOST_ONLY", "允许局域网 ROS", False, True),
    ("console", "CHASSIS_DEVICE", "底盘控制器设备", False, True),
    ("console", "UNILIDAR_PORT", "Unitree 雷达设备", False, False),
    ("console", "LIDAR_MOUNT_ROLL_RAD", "雷达安装横滚 Roll（弧度）", False, False),
    ("console", "LIDAR_MOUNT_PITCH_RAD", "雷达安装俯仰 Pitch（弧度）", False, False),
    ("console", "LIDAR_MOUNT_YAW_RAD", "雷达安装航向 Yaw（弧度）", False, False),
    ("console", "FRONT_CAMERA_DEVICE", "车头摄像头设备", False, True),
    ("console", "FRONT_CAMERA_ROTATION_DEGREES", "车头画面旋转角度", False, False),
    ("console", "FRONT_CAMERA_PREFER_NATIVE_MJPEG", "车头相机原生 MJPEG 直通", False, False),
    ("console", "FRONT_CAMERA_MANUAL_EXPOSURE", "车头相机固定帧率曝光", False, True),
    ("console", "FRONT_CAMERA_EXPOSURE_ABSOLUTE", "车头相机曝光时间（约 0.1 ms/级）", False, True),
    ("console", "FRONT_CAMERA_GAIN", "车头相机增益", False, True),
    ("console", "PROJECT_LINK_VOICE_ENABLE_MOTION", "语音运动授权", False, False),
    ("console", "PROJECT_LINK_VOICE_ENABLE_VISUAL_GRASP", "语音抓取授权", False, False),
    ("console", "PROJECT_LINK_QWEN_MODE", "Qwen 启动模式", False, True),
    ("voice_api", "PROJECT_LINK_ASR_PROVIDER", "经典 ASR 提供方", False, True),
    ("voice_api", "VOLCANO_ASR_ENDPOINT", "火山 ASR Endpoint", False, False),
    ("voice_api", "VOLCANO_ASR_RESOURCE_ID", "火山 ASR Resource ID", False, False),
    ("voice_api", "VOLCANO_ASR_API_KEY", "火山 ASR API Key", True, True),
    ("voice_api", "VOLCANO_ASR_APP_ID", "火山 ASR App ID", True, False),
    ("voice_api", "VOLCANO_ASR_ACCESS_TOKEN", "火山 ASR Access Token", True, False),
    ("voice_api", "DEEPSEEK_API_KEY", "DeepSeek API Key", True, True),
    ("voice_api", "VOLCANO_APP_ID", "豆包 TTS App ID", True, True),
    ("voice_api", "VOLCANO_ACCESS_TOKEN", "豆包 TTS Access Token", True, True),
    ("voice_api", "VOLCANO_SPEAKER", "豆包 TTS 音色", False, True),
    ("voice_api", "QWEATHER_API_KEY", "和风天气 API Key", True, False),
    ("qwen", "DASHSCOPE_API_KEY", "DashScope API Key", True, True),
    ("qwen", "QWEN_REALTIME_ENDPOINT", "Qwen Realtime Endpoint", False, True),
    ("qwen", "QWEN_REALTIME_MODEL", "Qwen Realtime 模型", False, True),
    ("qwen", "QWEN_REALTIME_VOICE", "Qwen 音色", False, True),
    ("qwen", "PROJECT_LINK_AUDIO_INPUT_NAME", "麦克风名称", False, True),
    ("qwen", "PROJECT_LINK_AUDIO_OUTPUT_DEVICE", "扬声器输出", False, True),
    ("qwen", "QWEATHER_API_KEY", "和风天气 API Key", True, False),
    ("fall_api", "FALL_GUARD_TOKEN", "手机跌倒守护共享 Token", True, True),
    ("fall_api", "OPENAI_API_KEY", "跌倒复核 VLM API Key", True, True),
    ("fall_api", "OPENAI_BASE_URL", "跌倒复核 VLM Endpoint", False, False),
    ("fall_api", "OPENAI_MODEL", "跌倒复核 VLM 模型", False, True),
    ("fall_api", "FALL_GATEWAY_PORT", "手机网关端口", False, True),
    ("fall_api", "FALL_SPECIALIZED_MODEL", "专用跌倒模型路径", False, False),
    ("fall_api", "FALL_WORLD_MODEL", "YOLO-World 模型路径", False, False),
    ("fall_api", "WECHATBOT_CREDENTIALS", "微信登录凭据路径", False, False),
    ("fall_api", "WECHATBOT_BINDING", "紧急联系人绑定路径", False, False),
    ("uwb", "PROJECT_LINK_UWB_DEVICE", "BU04 测距设备", False, True),
    ("uwb", "PROJECT_LINK_UWB_TAG_ADDRESS", "BU03 私有 Tag 地址", True, True),
]


class SettingsPage(QWidget):
    def __init__(self, config_client, parent=None) -> None:
        super().__init__(parent)
        self._client = config_client
        self._fields: dict[tuple[str, str], QLineEdit] = {}
        self._advanced_rows: list[tuple[QLabel, QLineEdit]] = []
        self._loaded = False
        self._show_uwb = os.environ.get("PROJECT_LINK_SHOW_UWB_PAGE", "0") == "1"

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        header = QHBoxLayout()
        title = QLabel("全局设置")
        title.setObjectName("pageTitle")
        self.state_label = QLabel("密钥只通过 SSH 写入 Orin")
        self.state_label.setObjectName("modeBadge")
        load_button = QPushButton("读取 Orin 配置")
        self.save_button = QPushButton("保存设置")
        self.save_button.setEnabled(False)
        load_button.clicked.connect(self._load)
        self.save_button.clicked.connect(self._save)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.state_label)
        header.addWidget(load_button)
        header.addWidget(self.save_button)
        root.addLayout(header)

        warning = QLabel(
            "API Key 不通过 ROS 话题传输，也不会回显。密钥框留空表示保持原值；"
            "保存只更新配置文件，不自动启动服务或授予运动权限。"
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("color: #d2ad72;")
        root.addWidget(warning)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(12, 12, 12, 12)
        connection = QGroupBox("Ubuntu 到 Orin 的配置通道")
        connection_form = QFormLayout(connection)
        self.ssh_target = QLineEdit(self._client.ssh_target)
        self.workspace = QLineEdit(self._client.workspace)
        self.ssh_target.setToolTip("仅用于固定配置助手，不用于执行任意命令")
        connection_form.addRow("SSH 目标", self.ssh_target)
        connection_form.addRow("Orin 工作区", self.workspace)
        layout.addWidget(connection)

        groups = {
            "console": QGroupBox("系统与设备"),
            "voice_api": QGroupBox("经典语音与豆包 / DeepSeek"),
            "qwen": QGroupBox("Qwen Realtime"),
            "fall_api": QGroupBox("跌倒检测、VLM 与微信通知"),
            "uwb": QGroupBox("UWB 私有配置"),
        }
        forms = {name: QFormLayout(box) for name, box in groups.items()}
        for file_name, key, label, secret, simple in FIELDS:
            if file_name == "uwb" and not self._show_uwb:
                continue
            widget = QLineEdit()
            widget.setToolTip(f"环境变量：{key}")
            if secret:
                widget.setEchoMode(QLineEdit.Password)
                widget.setPlaceholderText("未读取；留空保持原值")
            label_widget = QLabel(label)
            forms[file_name].addRow(label_widget, widget)
            self._fields[(file_name, key)] = widget
            if not simple:
                label_widget.setVisible(False)
                widget.setVisible(False)
                self._advanced_rows.append((label_widget, widget))
        for name in ("console", "voice_api", "qwen", "fall_api"):
            layout.addWidget(groups[name])
        if self._show_uwb:
            layout.addWidget(groups["uwb"])
        layout.addStretch()
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        self._client.loaded.connect(self._on_loaded)
        self._client.saved.connect(self._on_saved)
        self._client.failed.connect(self._on_failed)

    def _load(self) -> None:
        self._client.set_connection(self.ssh_target.text(), self.workspace.text())
        self.state_label.setText("正在读取…")
        self._client.load("global")

    def _on_loaded(self, section: str, data: dict) -> None:
        if section != "global":
            return
        files = data.get("files", {})
        for identity, widget in self._fields.items():
            file_name, key = identity
            value = files.get(file_name, {}).get(key, {})
            if value.get("secret"):
                widget.clear()
                widget.setPlaceholderText("已配置；留空保持原值" if value.get("configured") else "未配置")
            else:
                widget.setText(str(value.get("value", "")))
        self._loaded = True
        self.save_button.setEnabled(True)
        self.state_label.setText("已读取")

    def _save(self) -> None:
        self._client.set_connection(self.ssh_target.text(), self.workspace.text())
        payload = {
            "files": {
                "console": {},
                "voice_api": {},
                "qwen": {},
                "fall_api": {},
                "uwb": {},
            }
        }
        for (file_name, key), widget in self._fields.items():
            text = widget.text().strip()
            field = next(item for item in FIELDS if item[0] == file_name and item[1] == key)
            secret = field[3]
            if secret and not text:
                continue
            payload["files"][file_name][key] = text
        self.state_label.setText("正在保存…")
        self._client.save("global", payload)

    def _on_saved(self, section: str, _data: dict) -> None:
        if section == "global":
            self.state_label.setText("已保存；相关服务重启后生效")
            for file_name, key, _label, secret, _simple in FIELDS:
                if secret:
                    widget = self._fields.get((file_name, key))
                    if widget is None:
                        continue
                    if widget.text():
                        widget.clear()
                        widget.setPlaceholderText("已配置；留空保持原值")

    def _on_failed(self, section: str, message: str) -> None:
        if section == "global":
            self.state_label.setText("读取/保存失败")
            self.state_label.setToolTip(message)

    def set_advanced(self, enabled: bool) -> None:
        for label, widget in self._advanced_rows:
            label.setVisible(bool(enabled))
            widget.setVisible(bool(enabled))
