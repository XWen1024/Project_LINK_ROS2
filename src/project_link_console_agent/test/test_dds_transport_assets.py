from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
DDS = ROOT / "deploy" / "dds-router"


def test_dds_router_versions_are_commit_locked_and_user_isolated():
    versions = (DDS / "versions.env").read_text(encoding="utf-8")
    build = (DDS / "build-user-prefix.sh").read_text(encoding="utf-8")
    launcher = (DDS / "bin" / "project-link-console").read_text(encoding="utf-8")
    assert "DDS_ROUTER_VERSION=2.2.0" in versions
    assert "DDS_ROUTER_COMMIT=13172858f78adb76f09e8a5f7451517ba85b5652" in versions
    assert ".local/opt/project-link-dds-router/2.2.0" in versions
    assert "git -C \"$path\" checkout --detach \"$commit\"" in build
    assert "sudo" not in build
    assert "ROS_DOMAIN_ID=\"${PROJECT_LINK_CONSOLE_DOMAIN_ID:-142}\"" in launcher
    assert "project-link-dds-router-ubuntu.service" in launcher


def test_transport_is_loopback_tcp_domain_isolated_and_uwb_free():
    orin = (DDS / "config" / "orin.yaml").read_text(encoding="utf-8")
    ubuntu = (DDS / "config" / "ubuntu.yaml").read_text(encoding="utf-8")
    tunnel = (DDS / "systemd" / "project-link-dds-tunnel.service").read_text(
        encoding="utf-8"
    )
    assert "domain: 42" in orin
    assert "domain: 142" in ubuntu
    assert "ip: 127.0.0.1" in orin
    assert orin.count("whitelist-interfaces:") == 2
    assert ubuntu.count("whitelist-interfaces:") == 2
    assert "transport: tcp" in orin and "transport: tcp" in ubuntu
    assert "-L 127.0.0.1:11666:127.0.0.1:11666" in tunnel
    assert "uwb" not in orin.lower()
    assert "uwb" not in ubuntu.lower()
    assert "rt/front_camera/*" in orin
    assert "*visual_grasp*" in orin
