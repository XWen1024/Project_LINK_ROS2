from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_standalone_start_is_allowlisted_and_console_independent():
    helper = (
        ROOT / "deploy" / "systemd" / "bin" / "project-link-standalone-start"
    ).read_text(encoding="utf-8")
    assert "project-link-navigation.target" in helper
    assert "project-link-voice-qwen.service" in helper
    assert "project-link-emergency.target" in helper
    assert "project-link-console-agent" not in helper
    assert "systemctl --user start --no-block" in helper
    assert "ros2 " not in helper
    assert "/cmd_vel" not in helper


def test_nav2_standalone_start_restarts_the_complete_dependency_chain():
    helper = (
        ROOT / "deploy" / "systemd" / "bin" / "project-link-standalone-start"
    ).read_text(encoding="utf-8")
    assert 'if [[ "$component" == "nav2" ]]' in helper
    assert 'systemctl --user stop "$entry_unit" "${required_units[@]}"' in helper


def test_three_operator_wrappers_use_only_fixed_components():
    expected = {
        "start_nav2.sh": "nav2",
        "start_qwen_realtime.sh": "qwen-realtime",
        "start_fall_response.sh": "fall-response",
    }
    for name, component in expected.items():
        source = (ROOT / "scripts" / "standalone" / name).read_text(encoding="utf-8")
        assert "project-link-standalone-start" in source
        assert source.rstrip().endswith(component)
        assert "ssh " not in source


def test_three_client_wrappers_use_ssh_and_fixed_remote_scripts():
    names = (
        "ssh_start_nav2.sh",
        "ssh_start_qwen_realtime.sh",
        "ssh_start_fall_response.sh",
    )
    for name in names:
        source = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert "exec ssh" in source
        assert "BatchMode=yes" in source
        assert "PROJECT_LINK_ORIN_SSH_TARGET" in source
        assert "/home/wte/wheeltec_robot/scripts/standalone/" in source


def test_windows_double_click_wrappers_use_orin_alias():
    windows = ROOT / "scripts" / "windows"
    for name in (
        "start_nav2_over_ssh.cmd",
        "start_qwen_realtime_over_ssh.cmd",
        "start_fall_response_over_ssh.cmd",
    ):
        source = (windows / name).read_text(encoding="utf-8")
        assert "ssh orin /home/wte/wheeltec_robot/scripts/standalone/" in source
        assert "pause" in source
