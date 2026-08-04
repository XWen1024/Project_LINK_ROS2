import math
import unittest

from project_link_uwb_navigation.geometry import Calibration, base_to_map, sensor_to_base


class GeometryTests(unittest.TestCase):
    def test_calibration_and_robot_pose_transform(self) -> None:
        calibration = Calibration(
            status="valid",
            version="test",
            axis_xx=1.0,
            axis_xy=0.0,
            axis_yx=0.0,
            axis_yy=1.0,
            yaw_rad=0.0,
            translation_x_m=0.20,
            translation_y_m=0.0,
        )
        base_point = sensor_to_base(0.0, 2.0, calibration)
        self.assertAlmostEqual(base_point[0], 0.20)
        self.assertAlmostEqual(base_point[1], 2.0)
        map_point = base_to_map(*base_point, 1.0, 2.0, math.pi / 2.0)
        self.assertAlmostEqual(map_point[0], -1.0)
        self.assertAlmostEqual(map_point[1], 2.20)

    def test_invalid_calibration_fails_closed(self) -> None:
        calibration = Calibration("invalid", "none", 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0)
        with self.assertRaisesRegex(ValueError, "calibration_not_valid"):
            sensor_to_base(1.0, 0.0, calibration)
