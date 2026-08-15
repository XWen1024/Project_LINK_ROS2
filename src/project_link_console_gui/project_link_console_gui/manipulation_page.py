"""Embedded wrapper around the existing Ubuntu visual-grasp client."""

from __future__ import annotations

import traceback

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class ManipulationPage(QWidget):
    message = Signal(str)

    def __init__(self, demo: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._client = None
        self._panel = None
        self.initialization_error: str | None = None
        self.initialization_traceback: str | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        title = QLabel("机械臂与视觉抓取")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
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

    def set_advanced(self, enabled: bool) -> None:
        if self._panel is not None:
            self._panel.set_advanced(enabled)

    def shutdown(self) -> None:
        if self._panel is not None:
            self._panel.shutdown()
        if self._client is not None:
            self._client.destroy_node()
            self._client = None
