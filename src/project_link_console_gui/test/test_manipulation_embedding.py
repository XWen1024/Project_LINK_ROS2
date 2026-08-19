from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def test_visual_grasp_client_exposes_an_embeddable_panel():
    source = (
        REPOSITORY_ROOT
        / "src"
        / "project_link_visual_grasp_gui"
        / "project_link_visual_grasp_gui"
        / "app.py"
    ).read_text(encoding="utf-8")
    assert "class VisualGraspPanel(QWidget):" in source
    assert "class VisualGraspWindow(QMainWindow):" in source
    assert "self.panel = VisualGraspPanel" in source
    assert "class ParameterServiceClient:" in source
    assert "from rcl_interfaces.srv import GetParameters, SetParameters" in source
    assert "rclpy.parameter_client" not in source
    assert "future.result().results" in source
    assert "if not rclpy.ok():" in source
    assert "qos_profile_sensor_data" in source


def test_console_manipulation_page_only_constructs_a_remote_client():
    source = (
        REPOSITORY_ROOT
        / "src"
        / "project_link_console_gui"
        / "project_link_console_gui"
        / "manipulation_page.py"
    ).read_text(encoding="utf-8")
    assert "RemoteClient" in source
    assert "VisualGraspPanel" in source
    assert "start_approach" not in source
    assert "set_torque" not in source
    assert "self.initialization_error" in source
    assert "self.initialization_traceback" in source
    assert "start_visual_grasp" in source
    assert "不会启用扭矩" in source


def test_visual_grasp_server_declares_every_gui_parameter_and_uses_rclpy_logging():
    node = (
        REPOSITORY_ROOT
        / "src"
        / "project_link_visual_grasp"
        / "project_link_visual_grasp"
        / "node.py"
    ).read_text(encoding="utf-8")
    assert '"grasp_timeout_sec": 20.0' in node
    assert '"joint_command_limit": 95.0' in node
    assert 'get_logger().info(f"Opened camera' in node
    assert 'get_logger().info("Opened camera %s"' not in node
