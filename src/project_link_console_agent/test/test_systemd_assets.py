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


def test_console_agent_exposes_only_uwb_shadow_lifecycle_services():
    source = (
        REPOSITORY_ROOT
        / "src"
        / "project_link_console_agent"
        / "project_link_console_agent"
        / "node.py"
    ).read_text(encoding="utf-8")
    assert '"/project_link/console/start_uwb_shadow"' in source
    assert '"/project_link/console/stop_uwb_shadow"' in source
    assert "_systemd.start(UNITS[\"uwb_shadow\"])" in source
    assert "PersonNavigation" not in source
