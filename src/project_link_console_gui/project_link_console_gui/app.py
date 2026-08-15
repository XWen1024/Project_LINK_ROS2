"""Project LINK PySide6 console entrypoint."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import signal
import sys

from PySide6.QtCore import QProcess, QTimer, Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .navigation_page import NavigationPage
from .manipulation_page import ManipulationPage
from .config_client import ConfigClient
from .settings_page import SettingsPage
from .uwb_page import UwbPage
from .voice_config_page import VoiceConfigPage
from .voice_page import VoicePage


STYLE = """
QWidget { background: #15181d; color: #e6e9ed; font-size: 13px; }
QMainWindow { background: #111419; }
QListWidget { background: #111419; border: 0; padding: 10px; outline: 0; }
QListWidget::item { padding: 12px 14px; margin: 2px 0; border-radius: 6px; color: #aeb6c2; }
QListWidget::item:selected { background: #2b313a; color: white; }
QPushButton { background: #2a3038; border: 1px solid #3a424d; border-radius: 6px; padding: 8px 12px; }
QPushButton:hover { background: #343c46; }
QPushButton:pressed { background: #22272e; }
QPushButton:disabled { color: #69717b; background: #20242a; }
QPushButton#dangerButton { background: #7f2f31; border-color: #a44749; }
QGroupBox { border: 1px solid #343b45; border-radius: 7px; margin-top: 10px; padding-top: 12px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #cbd1d8; }
QLabel#pageTitle { font-size: 24px; font-weight: 600; }
QLabel#modeBadge { background: #26313a; border-radius: 10px; padding: 5px 10px; color: #8fd3ff; }
QTableWidget { background: #191d22; alternate-background-color: #1d2228; border: 1px solid #343b45; }
QHeaderView::section { background: #252b33; padding: 6px; border: 0; border-right: 1px solid #343b45; }
QPlainTextEdit { background: #101318; border: 1px solid #303741; color: #b7c0cb; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { background: #22272e; border: 1px solid #3a424d; border-radius: 4px; padding: 5px; }
QTabWidget::pane { border: 1px solid #343b45; border-radius: 6px; }
QTabBar::tab { background: #20252c; padding: 8px 16px; margin-right: 2px; }
QTabBar::tab:selected { background: #343c46; }
QFrame#statusCard { background: #1b2027; border: 1px solid #343b45; border-radius: 7px; }
QScrollArea { border: 0; }
"""


class PlaceholderPage(QWidget):
    def __init__(self, title: str, description: str, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        title_label = QLabel(title)
        title_label.setObjectName("pageTitle")
        body = QLabel(description)
        body.setWordWrap(True)
        body.setMaximumWidth(760)
        body.setStyleSheet("color: #aeb6c2; font-size: 14px; line-height: 1.5;")
        card = QFrame()
        card.setStyleSheet("QFrame { background: #1b1f25; border: 1px solid #343b45; border-radius: 8px; }")
        card_layout = QVBoxLayout(card)
        card_layout.addWidget(QLabel("页面骨架已建立，模块集成将在对应里程碑接入。"))
        card_layout.addWidget(body)
        layout.addWidget(title_label)
        layout.addWidget(card)
        layout.addStretch()

    def set_advanced(self, _enabled: bool) -> None:
        pass


class ConsoleWindow(QMainWindow):
    def __init__(self, bridge, demo: bool = False) -> None:
        super().__init__()
        self._bridge = bridge
        self._demo = demo
        self.config_client = ConfigClient(self)
        self.setWindowTitle("Project LINK 中控台" + (" — 离线演示" if demo else ""))
        self.resize(1500, 960)

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        sidebar = QWidget()
        sidebar.setFixedWidth(220)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(10, 16, 10, 12)
        brand = QLabel("PROJECT LINK\n灵犀中控")
        brand.setStyleSheet("font-size: 18px; font-weight: 600; padding: 8px 12px 18px 12px;")
        sidebar_layout.addWidget(brand)
        self.navigation = QListWidget()
        page_specs = [
            ("建图与导航", "建图、Navigation2、地图图层、打点导航和安全遥控。"),
            ("机械臂", "集成现有 Ubuntu 远程渲染客户端，Orin 继续独占相机、SO-101 和 ToF。"),
            ("语音控制", "经典链路与 Qwen Realtime 互斥切换、会话状态、精简日志和阶段耗时。"),
            ("语音配置", "VAD、常用参数、系统提示词和工具注册表；高级模式显示完整注释。"),
            ("远程召唤", "UWB shadow 距离、相对角度、残差趋势与四方向标定采集。"),
            ("全局设置", "统一管理设备、路径、ROS 网络和经过掩码保护的私密环境配置。"),
        ]
        for title, _description in page_specs:
            self.navigation.addItem(QListWidgetItem(title))
        sidebar_layout.addWidget(self.navigation, 1)
        self.advanced_toggle = QCheckBox("高级模式")
        sidebar_layout.addWidget(self.advanced_toggle)
        self.connection_label = QLabel("● 未连接")
        self.connection_label.setStyleSheet("color: #ef9a9a; padding: 8px;")
        sidebar_layout.addWidget(self.connection_label)
        root.addWidget(sidebar)

        content_splitter = QSplitter(Qt.Vertical)
        self.pages = QStackedWidget()
        self.navigation_page = NavigationPage(bridge)
        self.pages.addWidget(self.navigation_page)
        self.manipulation_page = ManipulationPage(demo=demo)
        self.manipulation_page.message.connect(self._append_log)
        self.pages.addWidget(self.manipulation_page)
        self.voice_page = VoicePage(bridge)
        self.voice_config_page = VoiceConfigPage(self.config_client)
        self.uwb_page = UwbPage(bridge, self.config_client)
        self.settings_page = SettingsPage(self.config_client)
        self.pages.addWidget(self.voice_page)
        self.pages.addWidget(self.voice_config_page)
        self.pages.addWidget(self.uwb_page)
        self.pages.addWidget(self.settings_page)
        content_splitter.addWidget(self.pages)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(800)
        self.log.setPlaceholderText("中控事件与简化运行日志")
        content_splitter.addWidget(self.log)
        content_splitter.setStretchFactor(0, 1)
        content_splitter.setStretchFactor(1, 0)
        content_splitter.setSizes([780, 150])
        root.addWidget(content_splitter, 1)
        self.setCentralWidget(central)

        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.navigation.setCurrentRow(0)
        self.advanced_toggle.toggled.connect(self._set_advanced)
        self.navigation_page.launch_rviz_requested.connect(self._launch_rviz)

        bridge.connection_changed.connect(self._connection_changed)
        bridge.system_state.connect(self.navigation_page.update_system_state)
        bridge.system_state.connect(self.voice_page.update_system_state)
        bridge.system_state.connect(self.uwb_page.update_system_state)
        bridge.grid_updated.connect(self.navigation_page.update_grid)
        bridge.scan_updated.connect(self.navigation_page.update_scan)
        bridge.cloud_updated.connect(self.navigation_page.update_cloud)
        bridge.path_updated.connect(self.navigation_page.update_path)
        bridge.robot_updated.connect(self.navigation_page.update_robot)
        bridge.operation_event.connect(self._append_log)
        bridge.console_event.connect(self._console_event)
        bridge.voice_status.connect(self.voice_page.update_voice_status)
        bridge.uwb_observation.connect(self.uwb_page.update_observation)
        bridge.uwb_status.connect(self.uwb_page.update_status)
        bridge.uwb_goal.connect(self.uwb_page.update_goal)

        if self.manipulation_page.initialization_error is not None:
            self._append_log(
                "机械臂页面初始化失败："
                + self.manipulation_page.initialization_error
            )
            if self.manipulation_page.initialization_traceback is not None:
                print(self.manipulation_page.initialization_traceback, file=sys.stderr)

    def _set_advanced(self, enabled: bool) -> None:
        for index in range(self.pages.count()):
            page = self.pages.widget(index)
            if hasattr(page, "set_advanced"):
                page.set_advanced(enabled)

    def _connection_changed(self, connected: bool, text: str) -> None:
        color = "#81c784" if connected else "#ef9a9a"
        self.connection_label.setText(("● " if connected else "○ ") + text)
        self.connection_label.setStyleSheet(f"color: {color}; padding: 8px;")
        self.navigation_page.set_connection_available(connected)
        self.voice_page.set_connection_available(connected)
        self.uwb_page.set_connection_available(connected)

    def _console_event(self, event: dict) -> None:
        self.voice_page.append_event(event)
        timing = ""
        if event.get("delta_ms") or event.get("total_ms"):
            timing = f" Δ{event.get('delta_ms', 0):.0f} ms / Σ{event.get('total_ms', 0):.0f} ms"
        self._append_log(
            f"[{event.get('subsystem', 'system')}] {event.get('phase', '')}{timing} {event.get('message', '')}"
        )

    def _append_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log.appendPlainText(f"{timestamp}  {message}")

    def _launch_rviz(self) -> None:
        arguments: list[str] = []
        try:
            from ament_index_python.packages import get_package_share_directory

            config = Path(get_package_share_directory("project_link_console_gui")) / "config" / "console.rviz"
            if config.is_file():
                arguments = ["-d", str(config)]
        except Exception:
            pass
        result = QProcess.startDetached("rviz2", arguments)
        started = result[0] if isinstance(result, tuple) else bool(result)
        if not started:
            QMessageBox.warning(self, "RViz2", "无法启动 rviz2，请检查 Ubuntu ROS 2 环境。")

    def closeEvent(self, event) -> None:
        self.manipulation_page.shutdown()
        self.config_client.shutdown()
        self._bridge.stop()
        event.accept()


def main() -> None:
    parser = argparse.ArgumentParser(description="Project LINK Ubuntu control console")
    parser.add_argument("--demo", action="store_true", help="Run with generated offline data")
    arguments, qt_arguments = parser.parse_known_args()
    app = QApplication([sys.argv[0], *qt_arguments])
    shutdown_requested = [False]

    def request_shutdown(*_args) -> None:
        shutdown_requested[0] = True
        app.quit()

    signal.signal(signal.SIGINT, request_shutdown)
    signal.signal(signal.SIGTERM, request_shutdown)
    signal_pump = QTimer()
    signal_pump.timeout.connect(lambda: None)
    signal_pump.start(250)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    if arguments.demo:
        from .demo import DemoBridge

        bridge = DemoBridge()
    else:
        from .ros_bridge import RosBridge

        bridge = RosBridge()
    bridge_error = None
    if not arguments.demo:
        try:
            bridge.start()
        except Exception as exc:
            bridge_error = exc
    window = ConsoleWindow(bridge, demo=arguments.demo)
    window.show()
    signal.signal(signal.SIGINT, lambda *_args: window.close())
    signal.signal(signal.SIGTERM, lambda *_args: window.close())
    if shutdown_requested[0]:
        QTimer.singleShot(0, window.close)
    if arguments.demo:
        bridge.start()
    elif bridge_error is not None:
        window._connection_changed(False, "ROS 初始化失败")
        window._append_log(f"ROS 初始化失败：{bridge_error}")
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
