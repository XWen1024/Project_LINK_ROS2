"""Qt-independent data models used by the console and its offline tests."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Iterable


@dataclass(frozen=True)
class GridLayer:
    width: int
    height: int
    resolution: float
    origin_x: float
    origin_y: float
    cells: tuple[int, ...]

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("grid dimensions must be positive")
        if not math.isfinite(self.resolution) or self.resolution <= 0.0:
            raise ValueError("grid resolution must be positive")
        if len(self.cells) != self.width * self.height:
            raise ValueError("grid cell count does not match dimensions")

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return (
            self.origin_x,
            self.origin_y,
            self.origin_x + self.width * self.resolution,
            self.origin_y + self.height * self.resolution,
        )

    def cell_to_world(self, column: int, row: int) -> tuple[float, float]:
        if not 0 <= column < self.width or not 0 <= row < self.height:
            raise IndexError("grid cell is outside the map")
        return (
            self.origin_x + (column + 0.5) * self.resolution,
            self.origin_y + (row + 0.5) * self.resolution,
        )


@dataclass(frozen=True)
class Pose2D:
    x: float
    y: float
    yaw: float = 0.0


@dataclass
class LayerVisibility:
    occupancy_map: bool = True
    global_costmap: bool = False
    local_costmap: bool = True
    laser_scan: bool = True
    point_cloud: bool = False
    path: bool = True


@dataclass
class TeleopKeyState:
    pressed: set[str] = field(default_factory=set)
    focused: bool = False

    def set_key(self, key: str, pressed: bool) -> None:
        normalized = key.lower()
        if pressed:
            self.pressed.add(normalized)
        else:
            self.pressed.discard(normalized)

    def clear(self) -> None:
        self.pressed.clear()
        self.focused = False

    def command(
        self,
        *,
        mapping_mode: bool,
        linear_speed: float,
        angular_speed: float,
    ) -> tuple[bool, bool, float, float]:
        enabled = bool(mapping_mode)
        deadman = enabled and self.focused and "space" in self.pressed
        if not deadman:
            return enabled, False, 0.0, 0.0
        forward = int("w" in self.pressed or "up" in self.pressed)
        backward = int("s" in self.pressed or "down" in self.pressed)
        left = int("a" in self.pressed or "left" in self.pressed)
        right = int("d" in self.pressed or "right" in self.pressed)
        return (
            enabled,
            True,
            (forward - backward) * abs(linear_speed),
            (left - right) * abs(angular_speed),
        )


def laser_points(
    ranges: Iterable[float],
    *,
    angle_min: float,
    angle_increment: float,
    range_min: float,
    range_max: float,
    step: int = 1,
) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    stride = max(1, int(step))
    for index, distance in enumerate(ranges):
        if index % stride:
            continue
        if not math.isfinite(distance) or distance < range_min or distance > range_max:
            continue
        angle = angle_min + index * angle_increment
        points.append((distance * math.cos(angle), distance * math.sin(angle)))
    return points


def transform_points(
    points: Iterable[tuple[float, float]], pose: Pose2D
) -> list[tuple[float, float]]:
    cosine = math.cos(pose.yaw)
    sine = math.sin(pose.yaw)
    return [
        (
            pose.x + cosine * x - sine * y,
            pose.y + sine * x + cosine * y,
        )
        for x, y in points
    ]
