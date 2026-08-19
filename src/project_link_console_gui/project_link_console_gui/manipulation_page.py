"""Embedded wrapper around the existing Ubuntu visual-grasp client."""

from __future__ import annotations

import traceback

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


class ManipulationPage(QWidget):
    message = Signal(str)

    def __init__(self, bridge, demo: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._client = None
        self._panel = None
        self._control_available = False
        self._service_ready = False
        self.initialization_error: str | None = None
        self.initialization_traceback: str | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        header = QHBoxLayout()
        title = QLabel("机械臂与视觉抓取")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch()
        self.lifecycle_status = QLabel("等待 Orin 机械臂服务")
        self.lifecycle_status.setObjectName("modeBadge")
        self.start_button = QPushButton("启动 Orin 视觉服务")
        self.stop_button = QPushButton("停止视觉服务")
        self.start_button.clicked.connect(self._bridge.start_visual_grasp)
        self.stop_button.clicked.connect(self._bridge.stop_visual_grasp)
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        header.addWidget(self.lifecycle_status)
        header.addWidget(self.start_button)
        header.addWidget(self.stop_button)
        layout.addLayout(header)
        note = QLabel(
            "启动视觉服务只加载 Orin 摄像头、模型和远程接口，不会自动连接 SO-101、"
            "不会启用扭矩，也不会产生机械臂运动。服务启动后再使用下方“连接”。"
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #8f99a6;")
        layout.addWidget(note)
        bridge.manipulation_control_available.connect(self.set_control_available)
        bridge.manipulation_operation.connect(self.show_operation)
        if demo:
            note = QLabel(
                "离线演示模式不创建机械臂 ROS client。真实模式下此页面复用现有 Ubuntu "
                "视觉抓取客户端，所有相机、YOLO、SO-101 和 ToF 仍由 Orin 独占。"
            )
            note.setWordWrap(True)
            layout.addWidget(note)
            layout.addStretch()
            return
        try:
            from project_link_visual_grasp_gui.app import RemoteClient, VisualGraspPanel

            self._client = RemoteClient()
            self._panel = VisualGraspPanel(
                self._client,
                show_advanced_parameters=False,
            )
            self._panel.message.connect(self.message.emit)
            layout.addWidget(self._panel, 1)
        except Exception as exc:
            self.initialization_error = f"{type(exc).__name__}: {exc}"
            self.initialization_traceback = traceback.format_exc()
            note = QLabel(f"机械臂页面初始化失败：{self.initialization_error}")
            note.setWordWrap(True)
            layout.addWidget(note)
            layout.addStretch()

    def set_control_available(self, available: bool, message: str) -> None:
        self._control_available = bool(available)
        self._refresh_lifecycle_buttons()
        self.lifecycle_status.setToolTip(message)

    def show_operation(self, message: str) -> None:
        self.lifecycle_status.setText(message)
        if self._panel is not None and "已启动" in message:
            QTimer.singleShot(1200, self._panel.refresh_remote)

    def update_system_state(self, state: dict) -> None:
        item = next(
            (
                value
                for value in state.get("subsystems", [])
                if value.get("name") == "project-link-visual-grasp.service"
            ),
            None,
        )
        if item is None:
            return
        self._service_ready = bool(item.get("ready"))
        self.lifecycle_status.setText(
            "视觉服务运行中" if self._service_ready else "视觉服务已停止"
        )
        self._refresh_lifecycle_buttons()

    def _refresh_lifecycle_buttons(self) -> None:
        self.start_button.setEnabled(self._control_available and not self._service_ready)
        self.stop_button.setEnabled(self._control_available and self._service_ready)

    def set_advanced(self, enabled: bool) -> None:
        if self._panel is not None:
            self._panel.set_advanced(enabled)

    def shutdown(self) -> None:
        if self._panel is not None:
            self._panel.shutdown()
        if self._client is not None:
            self._client.destroy_node()
            self._client = None
