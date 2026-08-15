import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from project_link_console_gui.app import ConsoleWindow
from project_link_console_gui.demo import DemoBridge


def test_window_fits_screen_and_sidebar_collapses_to_icons():
    app = QApplication.instance() or QApplication([])
    bridge = DemoBridge()
    window = ConsoleWindow(bridge, demo=True)
    available = app.primaryScreen().availableGeometry()

    assert window.width() <= available.width()
    assert window.height() <= available.height()
    assert window.pages.minimumSizeHint().height() == 320

    window.toggle_sidebar()
    assert window.sidebar.width() == 72
    assert window.navigation.item(0).text() == ""
    assert window.navigation.item(0).toolTip() == "建图与导航"

    window.toggle_sidebar()
    assert window.sidebar.width() == 220
    assert window.navigation.item(0).text() == "建图与导航"
    window.close()


def test_voice_controls_use_action_availability_not_only_state_heartbeat():
    app = QApplication.instance() or QApplication([])
    bridge = DemoBridge()
    window = ConsoleWindow(bridge, demo=True)
    page = window.voice_page

    page.set_connection_available(False)
    page.set_voice_control_available(True, "Orin 语音控制已连接")
    assert page.classic_button.isEnabled()
    assert page.qwen_button.isEnabled()

    page.set_voice_control_available(False, "等待发现 Orin 语音控制")
    assert not page.classic_button.isEnabled()
    assert not page.qwen_button.isEnabled()
    window.close()
