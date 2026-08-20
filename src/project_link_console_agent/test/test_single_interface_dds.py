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
