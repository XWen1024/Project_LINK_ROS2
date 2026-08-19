from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _source(name: str) -> str:
    return (PACKAGE_ROOT / "project_link_console_gui" / name).read_text(encoding="utf-8")


def test_all_planned_console_pages_are_real_widgets():
    app = _source("app.py")
    assert "VoicePage(bridge)" in app
    assert "VoiceConfigPage(self.config_client)" in app
    assert "FallResponsePage(bridge, self.config_client)" in app
    assert 'PROJECT_LINK_SHOW_UWB_PAGE", "0"' in app
    assert "if self._show_uwb_page:" in app
    bridge = _source("ros_bridge.py")
    assert "if self._uwb_enabled:" in bridge
    assert "SettingsPage(self.config_client)" in app
    assert "PlaceholderPage(title, description)" not in app


def test_navigation_page_has_separate_front_camera_preview():
    page = _source("navigation_page.py")
    bridge = _source("ros_bridge.py")
    assert 'QGroupBox("车头摄像头 · 原生 720P/16:9")' in page
    assert '"/front_camera/image/compressed"' in bridge
    assert "front_camera_image" in bridge
    assert "QImage.fromData(jpeg_data)" in page
    assert 'QImage.fromData(jpeg_data, b"JPG")' not in page
    assert "原生 720P/16:9" in page
    assert 'f"已连接 · {image.width()}×{image.height()} · {ratio} · {fps:.1f} FPS"' in page
    assert '"/visual_grasp/image/compressed"' not in bridge


def test_front_camera_exposure_is_advanced_and_uses_typed_parameter_services():
    page = _source("navigation_page.py")
    bridge = _source("ros_bridge.py")
    demo = _source("demo.py")
    assert 'QCheckBox("自动曝光（弱光时可能降低帧率）")' in page
    assert 'QPushButton("立即应用车头曝光")' in page
    assert "self.advanced_group.setVisible(self._advanced)" in page
    assert "self.camera_exposure.setEnabled(not automatic)" in page
    assert "self.camera_gain.setEnabled(not automatic)" in page
    assert '"/project_link_front_camera/get_parameters"' in bridge
    assert '"/project_link_front_camera/set_parameters"' in bridge
    assert "self._set_parameters_type.Request()" in bridge
    assert "self._parameter_type(" in bridge
    assert "shell" not in bridge
    assert "front_camera_parameters" in demo


def test_lidar_direction_calibration_is_preview_first_and_allowlisted():
    page = _source("navigation_page.py")
    bridge = _source("ros_bridge.py")
    helper = (
        REPOSITORY_ROOT / "scripts" / "project_link_console_config.py"
    ).read_text(encoding="utf-8")
    assert 'QGroupBox("雷达方向可视化标定")' in page
    assert 'QPushButton("保存并应用方向")' in page
    assert "set_lidar_preview_rpy" in page
    assert '"LIDAR_MOUNT_ROLL_RAD"' in page
    assert '"LIDAR_MOUNT_PITCH_RAD"' in page
    assert '"LIDAR_MOUNT_YAW_RAD"' in page
    assert '"/unilidar/cloud"' in bridge
    assert '"/project_link/lidar_calibration/cloud"' in bridge
    assert '"LIDAR_MOUNT_YAW_RAD": False' in helper
    assert "lidar_mount_angle_out_of_range" in helper
    assert "lidar_calibration.rviz" in _source("app.py")


def test_navigation_lifecycle_has_chinese_status_and_progress_dialog():
    page = _source("navigation_page.py")
    bridge = _source("ros_bridge.py")
    assert "class StackProgressDialog(QDialog):" in page
    assert '["功能", "模块名", "状态", "就绪"]' in page
    assert 'self.status_table.setColumnHidden(1, True)' in page
    assert '"正在检查就绪条件"' in page
    assert "stack_progress" in bridge


def test_voice_page_uses_typed_switch_action_via_bridge():
    page = _source("voice_page.py")
    bridge = _source("ros_bridge.py")
    assert '_request_switch(1, "经典链路")' in page
    assert '_request_switch(2, "Qwen Realtime")' in page
    assert "SwitchVoice" in bridge
    assert '"/project_link/console/switch_voice"' in bridge
    assert "probe_voice_control" in page
    assert "voice_control_available" in bridge
    assert "ROS_DOMAIN_ID=42" in page


def test_console_window_is_responsive_collapsible_and_single_instance():
    app = _source("app.py")
    assert "ResponsiveStackedWidget" in app
    assert "availableGeometry" in app
    assert "toggle_sidebar" in app
    assert "self.sidebar.setFixedWidth(72 if collapsed else 220)" in app
    assert "setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)" in app
    assert "QLockFile" in app
    assert "中控台已经在运行" in app
    assert "project-link-dds-tunnel.service" in app
    assert "project-link-dds-router-ubuntu.service" in app
    assert "SSH 隧道 + DDS Router 就绪" in app
    assert "connection_snapshot" in app
    assert 'self.config_client.load("global")' in app
    assert 'self.config_client.load("voice")' in app
    assert 'self.config_client.load("fall")' in app


def test_fall_response_page_uses_typed_status_and_allowlisted_controls():
    page = _source("fall_response_page.py")
    bridge = _source("ros_bridge.py")
    assert "跌倒检测与紧急响应" in page
    assert "真实 Nav2 分段旋转" in page
    assert "取消当前处置并停车" in page
    assert "FallResponseStatus" in bridge
    assert '"/fall_detection/status"' in bridge
    assert '"/fall_detection/get_event"' in bridge
    assert '"/fall_detection/list_events"' in bridge
    assert '"/cmd_vel"' not in page


def test_manipulator_lifecycle_is_explicit_and_never_enables_torque():
    page = _source("manipulation_page.py")
    bridge = _source("ros_bridge.py")
    assert "启动 Orin 视觉服务" in page
    assert "不会启用扭矩" in page
    assert "start_visual_grasp" in bridge
    assert "stop_visual_grasp" in bridge


def test_uwb_page_is_shadow_only_and_never_publishes_velocity():
    page = _source("uwb_page.py")
    bridge = _source("ros_bridge.py")
    assert "启动 Shadow" in page
    assert "proposed" in page
    assert '"/uwb/person_observation"' in bridge
    assert '"/cmd_vel"' not in page
    assert "PersonNavigation" not in page


def test_secret_configuration_uses_fixed_ssh_helper_and_stdin():
    client = _source("config_client.py")
    helper = (
        REPOSITORY_ROOT / "scripts" / "project_link_console_config.py"
    ).read_text(encoding="utf-8")
    assert 'process.setProgram("ssh")' in client
    assert "process.write(json.dumps" in client
    assert "shell=True" not in client
    assert "ENV_FILES" in helper
    assert '"secret": secret' in helper
    assert "API_KEY" not in client


def test_voice_profile_accepts_only_registered_executors():
    page = _source("voice_config_page.py")
    helper = (
        REPOSITORY_ROOT / "scripts" / "project_link_console_config.py"
    ).read_text(encoding="utf-8")
    assert "REGISTERED_TOOLS" in page
    assert "tool_not_registered_or_duplicate" in helper
    assert "任意命令" in page
