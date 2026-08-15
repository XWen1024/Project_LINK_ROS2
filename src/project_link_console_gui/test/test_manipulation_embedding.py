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
