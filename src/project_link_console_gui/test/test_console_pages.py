from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _source(name: str) -> str:
    return (PACKAGE_ROOT / "project_link_console_gui" / name).read_text(encoding="utf-8")


def test_all_planned_console_pages_are_real_widgets():
    app = _source("app.py")
    assert "VoicePage(bridge)" in app
    assert "VoiceConfigPage(self.config_client)" in app
    assert "UwbPage(bridge, self.config_client)" in app
    assert "SettingsPage(self.config_client)" in app
    assert "PlaceholderPage(title, description)" not in app


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
