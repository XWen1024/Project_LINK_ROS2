from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def test_gui_never_publishes_cmd_vel_directly():
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (PACKAGE_ROOT / "project_link_console_gui").glob("*.py")
    )
    assert '"/cmd_vel"' not in source
    assert '"/project_link/console/teleop"' in source
    assert "geometry_msgs.msg import Twist" not in source
    assert "set_connection_available" in source
    assert "_check_state_freshness" in source


def test_rviz_profile_contains_only_operator_diagnostics():
    profile = (PACKAGE_ROOT / "config" / "console.rviz").read_text(encoding="utf-8")
    assert "Fixed Frame: map" in profile
    assert "Value: /map" in profile
    assert "Value: /global_costmap/costmap" in profile
    assert "Value: /local_costmap/costmap" in profile
    assert "Value: /scan" in profile
    assert "Value: /point_lio/cloud_registered" in profile
