from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_runtime_fastdds_profile_is_single_interface_and_dynamic():
    helper = (ROOT / "scripts" / "project_link_dds_profile.sh").read_text(
        encoding="utf-8"
    )
    assert "ip -4 route get" in helper
    assert "PROJECT_LINK_DDS_PEER_IP" in helper
    assert "PROJECT_LINK_DDS_INTERFACE" in helper
    assert "<interfaceWhiteList>" in helper
    assert "<transport_id>project_link_shm</transport_id>" in helper
    assert "<type>SHM</type>" in helper
    assert "<useBuiltinTransports>false</useBuiltinTransports>" in helper
    assert 'export FASTRTPS_DEFAULT_PROFILES_FILE="$profile"' in helper


def test_orin_and_ubuntu_launchers_gate_fastdds_profile_explicitly():
    orin_env = (ROOT / "scripts" / "project_link_env.sh").read_text(encoding="utf-8")
    ubuntu_launcher = (
        ROOT / "deploy" / "dds-router" / "bin" / "project-link-console"
    ).read_text(encoding="utf-8")
    assert "project_link_dds_profile.sh" in orin_env
    assert "project_link_dds_profile.sh" in ubuntu_launcher
    assert "PROJECT_LINK_ENABLE_SINGLE_INTERFACE_DDS" in orin_env
    assert "PROJECT_LINK_ENABLE_SINGLE_INTERFACE_DDS" in ubuntu_launcher


def test_orin_environment_prefers_live_jetson_usb_link():
    orin_env = (ROOT / "scripts" / "project_link_env.sh").read_text(encoding="utf-8")
    assert "PROJECT_LINK_USB_CONSOLE_IP:-192.168.55.100" in orin_env
    assert "ping -I l4tbr0" in orin_env
    assert "PROJECT_LINK_DDS_INTERFACE=l4tbr0" in orin_env
    assert "PROJECT_LINK_TRANSPORT_MODE=usb-direct" in orin_env


def test_lidar_uses_the_shared_project_environment_before_its_overlay():
    component = (ROOT / "deploy/systemd/bin/project-link-component").read_text(
        encoding="utf-8"
    )
    lidar = component.split("  lidar)", 1)[1].split("  front-camera)", 1)[0]
    assert 'source "$workspace/scripts/project_link_env.sh"' in lidar
    assert lidar.index("project_link_env.sh") < lidar.index("unilidar_ws/install/setup.bash")


def test_ubuntu_launcher_prefers_the_jetson_usb_device_mode_link():
    launcher = (
        ROOT / "deploy" / "dds-router" / "bin" / "project-link-console"
    ).read_text(encoding="utf-8")
    assert "PROJECT_LINK_USB_ORIN_IP:-192.168.55.1" in launcher
    assert 'ping -I "$usb_interface"' in launcher
    assert 'PROJECT_LINK_ORIN_SSH_TARGET="wte@$usb_orin_ip"' in launcher
    assert "PROJECT_LINK_TRANSPORT_MODE=usb-direct" in launcher
