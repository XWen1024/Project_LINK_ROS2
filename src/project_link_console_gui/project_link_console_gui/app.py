"""Project LINK PySide6 console entrypoint."""

from __future__ import annotations

import argparse
from datetime import datetime
import os
from pathlib import Path
import signal
import sys

from PySide6.QtCore import QLockFile, QProcess, QSize, QStandardPaths, QTimer, Qt
from PySide6.QtGui import QKeySequence, QShortcut
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
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStyle,
    QToolButton,
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
QToolButton#sidebarToggle { background: transparent; border: 0; color: #aeb6c2; padding: 7px; }
QToolButton#sidebarToggle:hover { background: #252b33; border-radius: 6px; color: white; }
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


class ResponsiveStackedWidget(QStackedWidget):
    """Do not let a hidden page force the whole window beyond the desktop."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def minimumSizeHint(self) -> QSize:
        return QSize(480, 320)

    def sizeHint(self) -> QSize:
        current = self.currentWidget()
        if current is None:
            return QSize(1100, 720)
        hint = current.sizeHint()
        return QSize(min(hint.width(), 1200), min(hint.height(), 760))


class ConsoleWindow(QMainWindow):
    def __init__(self, bridge, demo: bool = False) -> None:
        super().__init__()
        self._bridge = bridge
        self._demo = demo
        self._sidebar_collapsed = False
        self._connection_text = "未连接"
        self._connection_ok = False
        self._subsystem_ready: dict[str, bool] | None = None
        self._transport_mode = os.environ.get("PROJECT_LINK_TRANSPORT_MODE", "legacy-dds")
        self._transport_process: QProcess | None = None
        self.config_client = ConfigClient(self)
        self.setWindowTitle("Project LINK 中控台" + (" — 离线演示" if demo else ""))

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.sidebar = QWidget()
        self.sidebar.setFixedWidth(220)
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(10, 16, 10, 12)
        self.sidebar_toggle = QToolButton()
        self.sidebar_toggle.setObjectName("sidebarToggle")
        self.sidebar_toggle.setText("«  收起导航")
        self.sidebar_toggle.setToolTip("收起导航栏（Ctrl+B）")
        self.sidebar_toggle.clicked.connect(self.toggle_sidebar)
        sidebar_layout.addWidget(self.sidebar_toggle)
        self.brand = QLabel("PROJECT LINK\n灵犀中控")
        self.brand.setStyleSheet("font-size: 18px; font-weight: 600; padding: 8px 12px 18px 12px;")
        sidebar_layout.addWidget(self.brand)
        self.navigation = QListWidget()
        self.navigation.setIconSize(QSize(22, 22))
        self.navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._show_uwb_page = os.environ.get("PROJECT_LINK_SHOW_UWB_PAGE", "0") == "1"
        page_specs = [
            ("建图与导航", QStyle.StandardPixmap.SP_DriveNetIcon),
            ("机械臂", QStyle.StandardPixmap.SP_ComputerIcon),
            ("语音控制", QStyle.StandardPixmap.SP_MediaVolume),
            ("语音配置", QStyle.StandardPixmap.SP_FileDialogDetailedView),
            ("全局设置", QStyle.StandardPixmap.SP_FileDialogContentsView),
        ]
        if self._show_uwb_page:
            page_specs.insert(-1, ("远程召唤", QStyle.StandardPixmap.SP_DialogResetButton))
        self._page_titles = [title for title, _icon in page_specs]
        for title, icon_name in page_specs:
            item = QListWidgetItem(self.style().standardIcon(icon_name), title)
            item.setToolTip(title)
            self.navigation.addItem(item)
        sidebar_layout.addWidget(self.navigation, 1)
        self.advanced_toggle = QCheckBox("高级模式")
        self.advanced_toggle.setToolTip("显示各页面的完整参数和诊断信息")
        sidebar_layout.addWidget(self.advanced_toggle)
        self.connection_label = QLabel("● 未连接")
        self.connection_label.setStyleSheet("color: #ef9a9a; padding: 8px;")
        sidebar_layout.addWidget(self.connection_label)
        self.transport_label = QLabel("⇄ 检查传输通道…")
        self.transport_label.setStyleSheet("color: #8e98a5; padding: 0 8px 8px 8px;")
        sidebar_layout.addWidget(self.transport_label)
        root.addWidget(self.sidebar)

        content_splitter = QSplitter(Qt.Vertical)
        self.pages = ResponsiveStackedWidget()
        self.navigation_page = NavigationPage(
            bridge, None if demo else self.config_client
        )
        self.pages.addWidget(self.navigation_page)
        self.manipulation_page = ManipulationPage(bridge, demo=demo)
        self.manipulation_page.message.connect(self._append_log)
        self.pages.addWidget(self.manipulation_page)
        self.voice_page = VoicePage(bridge)
        self.voice_config_page = VoiceConfigPage(self.config_client)
        self.uwb_page = None
        self.settings_page = SettingsPage(self.config_client)
        self.pages.addWidget(self.voice_page)
        self.pages.addWidget(self.voice_config_page)
        if self._show_uwb_page:
            self.uwb_page = UwbPage(bridge, self.config_client)
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
        self.navigation_page.launch_lidar_calibration_rviz_requested.connect(
            self._launch_lidar_calibration_rviz
        )

        bridge.connection_changed.connect(self._connection_changed)
        bridge.system_state.connect(self._system_state_changed)
        bridge.system_state.connect(self.navigation_page.update_system_state)
        bridge.system_state.connect(self.manipulation_page.update_system_state)
        bridge.system_state.connect(self.voice_page.update_system_state)
        if self.uwb_page is not None:
            bridge.system_state.connect(self.uwb_page.update_system_state)
        bridge.grid_updated.connect(self.navigation_page.update_grid)
        bridge.scan_updated.connect(self.navigation_page.update_scan)
        bridge.cloud_updated.connect(self.navigation_page.update_cloud)
        bridge.path_updated.connect(self.navigation_page.update_path)
        bridge.robot_updated.connect(self.navigation_page.update_robot)
        bridge.front_camera_image.connect(self.navigation_page.update_front_camera)
        bridge.operation_event.connect(self._append_log)
        bridge.console_event.connect(self._console_event)
        bridge.voice_status.connect(self.voice_page.update_voice_status)
        bridge.voice_control_available.connect(self.voice_page.set_voice_control_available)
        bridge.voice_operation.connect(self.voice_page.show_voice_operation)
        bridge.lifecycle_completed.connect(self._lifecycle_completed)
        if self.uwb_page is not None:
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

        self._sidebar_shortcut = QShortcut(QKeySequence("Ctrl+B"), self)
        self._sidebar_shortcut.activated.connect(self.toggle_sidebar)
        self._transport_timer = QTimer(self)
        self._transport_timer.setInterval(2000)
        self._transport_timer.timeout.connect(self._poll_transport)
        self._transport_timer.start()
        self._config_reload_timer = QTimer(self)
        self._config_reload_timer.setSingleShot(True)
        self._config_reload_timer.setInterval(500)
        self._config_reload_timer.timeout.connect(self._reload_configs)
        self._poll_transport()
        if hasattr(bridge, "connection_snapshot"):
            connected, text = bridge.connection_snapshot()
            self._connection_changed(connected, text)
        if not self._demo:
            self._config_reload_timer.start(0)
        self._fit_to_available_screen()

    def _reload_configs(self) -> None:
        self.config_client.load("global")
        self.config_client.load("voice")

    def _lifecycle_completed(self, _area: str, success: bool) -> None:
        if success and not self._demo:
            self._config_reload_timer.start(300)

    def _system_state_changed(self, state: dict) -> None:
        snapshot = {
            str(item.get("name", "")): bool(item.get("ready"))
            for item in state.get("subsystems", [])
        }
        previous = self._subsystem_ready
        self._subsystem_ready = snapshot
        if previous is None or self._demo:
            return
        newly_ready = any(
            ready and not previous.get(name, False)
            for name, ready in snapshot.items()
        )
        if newly_ready:
            self._config_reload_timer.start()

    def _fit_to_available_screen(self) -> None:
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            self.resize(1280, 800)
            return
        available = screen.availableGeometry()
        width = min(1500, max(900, int(available.width() * 0.92)))
        height = min(960, max(620, int(available.height() * 0.90)))
        self.resize(min(width, available.width()), min(height, available.height()))
        frame = self.frameGeometry()
        frame.moveCenter(available.center())
        self.move(frame.topLeft())

    def toggle_sidebar(self) -> None:
        self._sidebar_collapsed = not self._sidebar_collapsed
        collapsed = self._sidebar_collapsed
        self.sidebar.setFixedWidth(72 if collapsed else 220)
        self.brand.setVisible(not collapsed)
        self.sidebar_toggle.setText("»" if collapsed else "«  收起导航")
        self.sidebar_toggle.setToolTip(
            "展开导航栏（Ctrl+B）" if collapsed else "收起导航栏（Ctrl+B）"
        )
        self.advanced_toggle.setText("" if collapsed else "高级模式")
        self.connection_label.setText(
            ("●" if self._connection_ok else "○")
            if collapsed
            else (("● " if self._connection_ok else "○ ") + self._connection_text)
        )
        self.connection_label.setToolTip(self._connection_text)
        self.transport_label.setText(
            "⇄" if collapsed else f"⇄ {self.transport_label.toolTip()}"
        )
        self.navigation.setStyleSheet(
            "QListWidget { padding: 7px; } QListWidget::item { padding: 12px 8px; }"
            if collapsed else ""
        )
        for index, title in enumerate(self._page_titles):
            item = self.navigation.item(index)
            item.setText("" if collapsed else title)
            item.setTextAlignment(Qt.AlignCenter if collapsed else Qt.AlignVCenter | Qt.AlignLeft)

    def _set_advanced(self, enabled: bool) -> None:
        for index in range(self.pages.count()):
            page = self.pages.widget(index)
            if hasattr(page, "set_advanced"):
                page.set_advanced(enabled)

    def _connection_changed(self, connected: bool, text: str) -> None:
        self._connection_ok = bool(connected)
        self._connection_text = text
        color = "#81c784" if connected else "#ef9a9a"
        self.connection_label.setText(
            ("●" if connected else "○")
            if self._sidebar_collapsed
            else (("● " if connected else "○ ") + text)
        )
        self.connection_label.setToolTip(text)
        self.connection_label.setStyleSheet(f"color: {color}; padding: 8px;")
        self.navigation_page.set_connection_available(connected)
        self.voice_page.set_connection_available(connected)
        if self.uwb_page is not None:
            self.uwb_page.set_connection_available(connected)

    def _poll_transport(self) -> None:
        if self._transport_mode != "dds-router":
            message = "直连 DDS 兼容模式"
            self.transport_label.setToolTip(message)
            self.transport_label.setText("⇄" if self._sidebar_collapsed else f"⇄ {message}")
            self.transport_label.setStyleSheet("color: #d2ad72; padding: 0 8px 8px 8px;")
            return
        if self._transport_process is not None:
            return
        process = QProcess(self)
        process.setProgram("systemctl")
        process.setArguments(
            [
                "--user",
                "show",
                "project-link-dds-tunnel.service",
                "project-link-dds-router-ubuntu.service",
                "--property=Id",
                "--property=ActiveState",
                "--property=SubState",
                "--no-pager",
            ]
        )
        process.finished.connect(self._transport_finished)
        self._transport_process = process
        process.start()

    def _transport_finished(self, exit_code: int, _exit_status) -> None:
        process = self._transport_process
        self._transport_process = None
        output = "" if process is None else bytes(process.readAllStandardOutput()).decode(
            "utf-8", errors="replace"
        )
        if process is not None:
            process.deleteLater()
        active = {
            line.partition("=")[2]
            for line in output.splitlines()
            if line.startswith("Id=")
        }
        active_count = output.count("ActiveState=active")
        ready = exit_code == 0 and active_count == 2 and len(active) == 2
        message = "SSH 隧道 + DDS Router 就绪" if ready else "等待 SSH 隧道 / DDS Router"
        self.transport_label.setToolTip(message)
        self.transport_label.setText("⇄" if self._sidebar_collapsed else f"⇄ {message}")
        color = "#81c784" if ready else "#ef9a9a"
        self.transport_label.setStyleSheet(f"color: {color}; padding: 0 8px 8px 8px;")

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
        self._launch_rviz_config("console.rviz")

    def _launch_lidar_calibration_rviz(self) -> None:
        self._launch_rviz_config("lidar_calibration.rviz")

    def _launch_rviz_config(self, config_name: str) -> None:
        arguments: list[str] = []
        try:
            from ament_index_python.packages import get_package_share_directory

            config = (
                Path(get_package_share_directory("project_link_console_gui"))
                / "config"
                / config_name
            )
            if config.is_file():
                arguments = ["-d", str(config)]
        except Exception:
            pass
        result = QProcess.startDetached("rviz2", arguments)
        started = result[0] if isinstance(result, tuple) else bool(result)
        if not started:
            QMessageBox.warning(self, "RViz2", "无法启动 rviz2，请检查 Ubuntu ROS 2 环境。")

    def closeEvent(self, event) -> None:
        if self._transport_process is not None:
            self._transport_process.kill()
        self.manipulation_page.shutdown()
        self.config_client.shutdown()
        self._bridge.stop()
        event.accept()


def main() -> None:
    parser = argparse.ArgumentParser(description="Project LINK Ubuntu control console")
    parser.add_argument("--demo", action="store_true", help="Run with generated offline data")
    arguments, qt_arguments = parser.parse_known_args()
    app = QApplication([sys.argv[0], *qt_arguments])
    instance_lock = None
    if not arguments.demo:
        runtime_dir = QStandardPaths.writableLocation(QStandardPaths.RuntimeLocation)
        if not runtime_dir:
            runtime_dir = QStandardPaths.writableLocation(QStandardPaths.TempLocation)
        lock_name = f"project-link-console-{os.getuid() if hasattr(os, 'getuid') else 'user'}.lock"
        instance_lock = QLockFile(str(Path(runtime_dir) / lock_name))
        instance_lock.setStaleLockTime(5000)
        if not instance_lock.tryLock(100):
            QMessageBox.information(
                None,
                "Project LINK 中控台",
                "中控台已经在运行。请切换到现有窗口，不要重复启动多个实例。",
            )
            return
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
