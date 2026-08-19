from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def test_front_camera_is_orin_owned_and_uses_a_dedicated_topic():
    source = (
        PACKAGE_ROOT / "project_link_console_agent" / "front_camera.py"
    ).read_text(encoding="utf-8")
    assert '"/dev/project_link_front_camera"' in source
    assert '"/front_camera/image/compressed"' in source
    assert '"/front_camera/capture_still"' in source
    assert "CaptureStill" in source
    assert 'self.declare_parameter("camera_width", 1280)' in source
    assert "CompressedImage" in source
    assert '"/visual_grasp/image/compressed"' not in source
    assert '"/cmd_vel"' not in source


def test_front_camera_service_is_optional_to_platform_motion_stack():
    target = (
        REPOSITORY_ROOT / "deploy" / "systemd" / "user" / "project-link-platform.target"
    ).read_text(encoding="utf-8")
    unit = (
        REPOSITORY_ROOT
        / "deploy"
        / "systemd"
        / "user"
        / "project-link-front-camera.service"
    ).read_text(encoding="utf-8")
    assert "Wants=project-link-front-camera.service" in target
    assert "Requires=project-link-front-camera.service" not in target
    assert "front-camera" in unit
    assert "/front_camera/image/compressed" in unit


def test_production_hardware_rules_separate_chassis_wakeup_and_cameras():
    rules = (
        REPOSITORY_ROOT / "config" / "udev" / "99-project-link-hardware.rules"
    ).read_text(encoding="utf-8")
    assert 'ATTRS{serial}=="5B1F024697"' in rules
    assert 'ATTRS{serial}=="0004"' in rules
    assert 'SYMLINK+="project_link_chassis"' in rules
    assert 'SYMLINK+="project_link_wakeup"' in rules
    assert 'SYMLINK+="project_link_front_camera"' in rules
    assert 'SYMLINK+="project_link_arm_camera"' in rules
