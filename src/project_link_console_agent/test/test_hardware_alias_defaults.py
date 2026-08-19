from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_runtime_defaults_use_project_specific_hardware_aliases():
    base = (ROOT / "src/turn_on_wheeltec_robot/launch/base_serial.launch.py").read_text(
        encoding="utf-8"
    )
    helper = (ROOT / "scripts/project_link_console_config.py").read_text(encoding="utf-8")
    settings = (
        ROOT
        / "src/project_link_console_gui/project_link_console_gui/settings_page.py"
    ).read_text(encoding="utf-8")
    assert "default_value='/dev/project_link_chassis'" in base
    assert '"UNILIDAR_PORT": "/dev/project_link_lidar"' in helper
    assert '"FRONT_CAMERA_DEVICE": "/dev/project_link_front_camera"' in helper
    assert '"CHASSIS_DEVICE", "底盘控制器设备"' in settings
