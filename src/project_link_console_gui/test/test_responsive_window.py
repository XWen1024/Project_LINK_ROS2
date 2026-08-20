import os
import math

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


def test_front_camera_exposure_controls_follow_advanced_and_automatic_modes():
    app = QApplication.instance() or QApplication([])
    bridge = DemoBridge()
    window = ConsoleWindow(bridge, demo=True)
    page = window.navigation_page

    assert page.advanced_group.isHidden()
    page.set_advanced(True)
    assert not page.advanced_group.isHidden()
    assert page.camera_exposure.isEnabled()
    assert page.camera_gain.isEnabled()
    assert not page.camera_white_balance.isEnabled()

    page.camera_auto_exposure.setChecked(True)
    assert not page.camera_exposure.isEnabled()
    assert not page.camera_gain.isEnabled()

    page.camera_auto_white_balance.setChecked(False)
    assert page.camera_white_balance.isEnabled()

    page.camera_apply.click()
    assert page.camera_apply.isEnabled()
    assert page.camera_config_status.text() == "演示相机参数已应用"
    window.close()


def test_lidar_calibration_slider_only_changes_demo_preview():
    app = QApplication.instance() or QApplication([])
    bridge = DemoBridge()
    window = ConsoleWindow(bridge, demo=True)
    page = window.navigation_page

    page.set_advanced(True)
    page.lidar_axis_degrees["roll"].setValue(4.5)
    page.lidar_axis_degrees["pitch"].setValue(82.0)
    page.lidar_axis_degrees["yaw"].setValue(137.5)
    assert page.lidar_axis_sliders["roll"].value() == 45
    assert page.lidar_axis_sliders["pitch"].value() == 820
    assert page.lidar_axis_sliders["yaw"].value() == 1375
    assert "仅预览" in page.lidar_calibration_status.text()
    assert bridge._lidar_preview_rpy[0] > 0.0

    page.lidar_reset_preview.click()
    assert math.isclose(page.lidar_axis_degrees["roll"].value(), -90.0, abs_tol=0.1)
    assert math.isclose(page.lidar_axis_degrees["pitch"].value(), -2.2, abs_tol=0.1)
    assert math.isclose(page.lidar_axis_degrees["yaw"].value(), 90.0, abs_tol=0.1)
    window.close()
