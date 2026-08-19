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
    assert 'self.declare_parameter("camera_height", 720)' in source
    assert 'self.declare_parameter("camera_fps", 30.0)' in source
    assert 'self.declare_parameter("preview_fps", 30.0)' in source
    assert 'self.declare_parameter("preview_width", 1280)' in source
    assert 'self.declare_parameter("preview_height", 720)' in source
    assert 'name="front-camera-capture"' in source
    assert "def _capture_loop(self)" in source
    assert "def _publish_preview(self)" in source
    assert "period = 0.5 / max" in source
    assert 'self.declare_parameter("prefer_native_mjpeg", True)' in source
    assert 'self.declare_parameter("manual_exposure", True)' in source
    assert 'self.declare_parameter("exposure_time_absolute", 300)' in source
    assert 'self.declare_parameter("camera_gain", 32)' in source
    assert '"--stream-to=-"' in source
    assert '"native_mjpeg_zero_reencode"' in source
    assert '"auto_exposure=1,exposure_time_absolute={exposure},gain={gain}"' in source
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


def test_native_mjpeg_parser_drops_truncated_frame_before_next_soi():
    from project_link_console_agent.front_camera import FrontCameraNode

    valid = b"\xff\xd8" + b"x" * 2048 + b"\xff\xd9"
    buffer = bytearray(b"noise\xff\xd8truncated" + valid)
    assert FrontCameraNode._pop_native_jpeg(buffer) == valid


def test_front_camera_runtime_exposure_parameters_are_bounded():
    source = (
        PACKAGE_ROOT / "project_link_console_agent" / "front_camera.py"
    ).read_text(encoding="utf-8")
    assert 'parameter.name == "manual_exposure"' in source
    assert "manual_exposure must be boolean" in source
    assert "exposure_time_absolute must be between 1 and 5000" in source
    assert "camera_gain must be between 0 and 63" in source
    assert 'controls = "auto_exposure=3"' in source


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
