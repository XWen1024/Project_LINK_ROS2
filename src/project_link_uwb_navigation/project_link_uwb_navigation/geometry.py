"""Calibration and planar coordinate transforms for UWB targets."""

from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Calibration:
    status: str
    version: str
    axis_xx: float
    axis_xy: float
    axis_yx: float
    axis_yy: float
    yaw_rad: float
    translation_x_m: float
    translation_y_m: float

    def validate(self, require_approved: bool = True) -> None:
        if require_approved and self.status != "valid":
            raise ValueError("calibration_not_valid")
        if not require_approved and self.status not in ("valid", "proposed", "invalid"):
            raise ValueError("calibration_status_unknown")
        values = (
            self.axis_xx,
            self.axis_xy,
            self.axis_yx,
            self.axis_yy,
            self.yaw_rad,
            self.translation_x_m,
            self.translation_y_m,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("calibration_non_finite")
        determinant = self.axis_xx * self.axis_yy - self.axis_xy * self.axis_yx
        if not math.isclose(abs(determinant), 1.0, rel_tol=0.0, abs_tol=1e-6):
            raise ValueError("calibration_axis_matrix_invalid")


def sensor_to_base(
    x_m: float,
    y_m: float,
    calibration: Calibration,
    require_approved: bool = True,
) -> tuple[float, float]:
    calibration.validate(require_approved=require_approved)
    axis_x = calibration.axis_xx * x_m + calibration.axis_xy * y_m
    axis_y = calibration.axis_yx * x_m + calibration.axis_yy * y_m
    cosine = math.cos(calibration.yaw_rad)
    sine = math.sin(calibration.yaw_rad)
    return (
        cosine * axis_x - sine * axis_y + calibration.translation_x_m,
        sine * axis_x + cosine * axis_y + calibration.translation_y_m,
    )


def base_to_map(
    point_x_m: float,
    point_y_m: float,
    robot_x_m: float,
    robot_y_m: float,
    robot_yaw_rad: float,
) -> tuple[float, float]:
    cosine = math.cos(robot_yaw_rad)
    sine = math.sin(robot_yaw_rad)
    return (
        robot_x_m + cosine * point_x_m - sine * point_y_m,
        robot_y_m + sine * point_x_m + cosine * point_y_m,
    )


def yaw_from_quaternion(x: float, y: float, z: float, w: float) -> float:
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))
