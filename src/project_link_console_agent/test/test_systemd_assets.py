from pathlib import Path

from project_link_console_agent.systemd import UNITS


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEPLOY_ROOT = REPOSITORY_ROOT / "deploy" / "systemd"


def test_every_allowlisted_unit_is_versioned():
    unit_dir = DEPLOY_ROOT / "user"
    missing = [unit for unit in UNITS.values() if not (unit_dir / unit).is_file()]
    assert not missing


def test_systemd_deployment_does_not_use_tmux_or_nonzero_velocity():
    deployment_text = "\n".join(
        path.read_text(encoding="utf-8")
        for root in (DEPLOY_ROOT / "bin", DEPLOY_ROOT / "user")
        for path in root.rglob("*")
        if path.is_file()
    )
    assert "tmux " not in deployment_text
    assert "linear: {x:" not in deployment_text
    assert "angular: {z:" not in deployment_text


def test_targets_preserve_mapping_when_navigation_stops():
    unit_dir = DEPLOY_ROOT / "user"
    navigation = (unit_dir / UNITS["navigation_target"]).read_text(encoding="utf-8")
    nav2 = (unit_dir / UNITS["nav2"]).read_text(encoding="utf-8")
    mapping = (unit_dir / UNITS["mapping_target"]).read_text(encoding="utf-8")
    assert f"Requires={UNITS['mapping_target']} {UNITS['nav2']}" in navigation
    assert f"PartOf={UNITS['navigation_target']}" in nav2
    assert f"PartOf={UNITS['navigation_target']}" not in mapping


def test_mutually_exclusive_modes_share_a_neutral_platform_target():
    unit_dir = DEPLOY_ROOT / "user"
    mapping = (unit_dir / UNITS["mapping_target"]).read_text(encoding="utf-8")
    rf2o = (unit_dir / UNITS["rf2o_target"]).read_text(encoding="utf-8")
    assert UNITS["platform_target"] in mapping
    assert UNITS["platform_target"] in rf2o
    for key in ("base", "lidar", "robot_description", "scan"):
        source = (unit_dir / UNITS[key]).read_text(encoding="utf-8")
        assert f"PartOf={UNITS['platform_target']}" in source
        assert f"PartOf={UNITS['mapping_target']}" not in source
        assert f"PartOf={UNITS['rf2o_target']}" not in source


def test_console_agent_unit_retries_ros_discovery_before_ready():
    unit = (
        DEPLOY_ROOT / "user" / UNITS["agent"]
    ).read_text(encoding="utf-8")
    assert "project-link-wait topic /project_link/console/system_state 20" in unit


def test_topic_readiness_ignores_stale_ros2_cli_daemon_state():
    wait_helper = (DEPLOY_ROOT / "bin" / "project-link-wait").read_text(
        encoding="utf-8"
    )
    assert "ros2 topic echo --no-daemon --once" in wait_helper


def test_accumulated_scan_waits_for_point_lio_odom_without_platform_cycle():
    unit_dir = DEPLOY_ROOT / "user"
    scan = (unit_dir / UNITS["scan"]).read_text(encoding="utf-8")
    point_lio = (unit_dir / UNITS["point_lio_map"]).read_text(encoding="utf-8")
    assert "topic /scan 30" in scan
    assert "/scan_accumulated" not in scan
    assert "topic /odom_lio 45" in point_lio
    assert "topic /scan_accumulated 45" in point_lio


def test_console_reactivates_mode_target_after_services_recover():
    source = (
        REPOSITORY_ROOT
        / "src"
        / "project_link_console_agent"
        / "project_link_console_agent"
        / "node.py"
    ).read_text(encoding="utf-8")
    assert "dependencies_ready = all(" in source
    assert "依赖功能已就绪，正在重新确认模式状态" in source
    assert "self._systemd.start_no_block(target)" in source


def test_lidar_chassis_yaw_and_lio_projection_use_the_same_clockwise_correction():
    urdf = (
        REPOSITORY_ROOT
        / "src"
        / "turn_on_wheeltec_robot"
        / "urdf"
        / "patrol_robot.urdf.xacro"
    ).read_text(encoding="utf-8")
    projection = (
        REPOSITORY_ROOT / "configs" / "point_lio" / "lio_planar_projection.yaml"
    ).read_text(encoding="utf-8")
    assert 'lidar_mount_rpy" value="0.0 1.5708 1.5707963268"' in urdf
    assert "lio_to_base_x: -0.0883684513" in projection
    assert "lio_to_base_y: 0.6641805860" in projection
    assert "lio_to_base_roll: -1.5707950540" in projection
    assert "lio_to_base_yaw: -2.7011826536" in projection


def test_runtime_voice_and_uwb_overrides_remain_local_and_shadow_only():
    component = (DEPLOY_ROOT / "bin" / "project-link-component").read_text(encoding="utf-8")
    assert "PROJECT_LINK_VOICE_PROFILE" in component
    assert "voice_classic.yaml" in component
    assert "voice_qwen.yaml" in component
    assert "uwb_navigation.yaml" in component
    assert "enable_motion:=false" in component


def test_front_camera_component_uses_the_stable_alias():
    component = (REPOSITORY_ROOT / "deploy/systemd/bin/project-link-component").read_text(
        encoding="utf-8"
    )
    assert 'FRONT_CAMERA_DEVICE:-/dev/project_link_front_camera' in component
    assert 'UNILIDAR_PORT:-/dev/project_link_lidar' in component
    assert 'CHASSIS_DEVICE:-/dev/project_link_chassis' in component
    assert 'FRONT_CAMERA_PREFER_NATIVE_MJPEG:-true' in component
    assert 'FRONT_CAMERA_EXPOSURE_ABSOLUTE:-300' in component
    assert 'FRONT_CAMERA_GAIN:-32' in component


def test_lidar_component_sources_humble_overlays_without_nounset():
    component = (DEPLOY_ROOT / "bin" / "project-link-component").read_text(
        encoding="utf-8"
    )
    lidar_block = component.split("  lidar)", 1)[1].split("    ;;", 1)[0]
    assert "set +u\n    source /opt/ros/humble/setup.bash" in lidar_block
    assert 'source "$unilidar_ws/install/setup.bash"\n    set -u' in lidar_block


def test_console_agent_exposes_allowlisted_optional_lifecycle_services():
    source = (
        REPOSITORY_ROOT
        / "src"
        / "project_link_console_agent"
        / "project_link_console_agent"
        / "node.py"
    ).read_text(encoding="utf-8")
    assert '"/project_link/console/start_uwb_shadow"' in source
    assert '"/project_link/console/stop_uwb_shadow"' in source
    assert '"/project_link/console/start_visual_grasp"' in source
    assert '"/project_link/console/stop_visual_grasp"' in source
    assert "_systemd.start(UNITS[\"uwb_shadow\"])" in source
    assert "_systemd.start(UNITS[\"visual_grasp\"])" in source
    assert "PersonNavigation" not in source
