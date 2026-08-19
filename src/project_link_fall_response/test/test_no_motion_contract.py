from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def test_mobile_backend_uses_nav2_action_and_never_publishes_cmd_vel():
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (PACKAGE_ROOT / "project_link_fall_response").glob("*.py")
        if path.name not in {"fall_response_node.py"}
    )
    assert 'create_publisher(Twist' not in source
    assert "from nav2_msgs.action import Spin" in source
    assert "ActionClient(" in source
    assert "Spin," in source
    assert "create_publisher(\n            Twist" not in source


def test_emergency_target_does_not_require_motion_stack():
    target = (
        REPOSITORY_ROOT / "deploy/systemd/user/project-link-emergency.target"
    ).read_text(encoding="utf-8")
    for forbidden in ("project-link-base", "project-link-lidar", "project-link-nav2"):
        assert forbidden not in target
