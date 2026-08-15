import math

from project_link_console_gui.models import (
    GridLayer,
    Pose2D,
    TeleopKeyState,
    laser_points,
    transform_points,
)


def test_grid_bounds_and_cell_center():
    grid = GridLayer(2, 3, 0.5, -1.0, 2.0, (0, 0, 0, 0, 0, 100))
    assert grid.bounds == (-1.0, 2.0, 0.0, 3.5)
    assert grid.cell_to_world(1, 2) == (-0.25, 3.25)


def test_teleop_requires_mapping_focus_and_deadman():
    keys = TeleopKeyState(focused=True)
    keys.set_key("space", True)
    keys.set_key("w", True)
    keys.set_key("a", True)
    assert keys.command(mapping_mode=True, linear_speed=0.12, angular_speed=0.4) == (
        True,
        True,
        0.12,
        0.4,
    )
    assert keys.command(mapping_mode=False, linear_speed=0.12, angular_speed=0.4) == (
        False,
        False,
        0.0,
        0.0,
    )


def test_laser_filter_and_transform():
    points = laser_points(
        [float("inf"), 1.0, 3.0],
        angle_min=0.0,
        angle_increment=math.pi / 2.0,
        range_min=0.1,
        range_max=2.0,
    )
    transformed = transform_points(points, Pose2D(2.0, 3.0, math.pi / 2.0))
    assert len(transformed) == 1
    assert math.isclose(transformed[0][0], 1.0, abs_tol=1e-6)
    assert math.isclose(transformed[0][1], 3.0, abs_tol=1e-6)
