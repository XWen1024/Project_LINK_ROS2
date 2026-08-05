"""Pure summon/follow goal policy; this module never calls ROS or publishes velocity."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import math


class PersonMode(IntEnum):
    SUMMON = 1
    FOLLOW = 2


@dataclass(frozen=True)
class PolicyConfig:
    summon_distance_m: float = 1.0
    summon_min_distance_m: float = 0.75
    summon_arrival_distance_m: float = 1.15
    follow_distance_m: float = 1.5
    follow_min_distance_m: float = 1.3
    follow_hold_distance_m: float = 1.7

    def validate(self) -> None:
        if not (0.0 < self.summon_min_distance_m <= self.summon_distance_m <= self.summon_arrival_distance_m):
            raise ValueError("invalid_summon_distances")
        if not (0.0 < self.follow_min_distance_m <= self.follow_distance_m <= self.follow_hold_distance_m):
            raise ValueError("invalid_follow_distances")


@dataclass(frozen=True)
class GoalDecision:
    action: str
    reason: str
    person_distance_m: float
    goal_x_m: float | None = None
    goal_y_m: float | None = None
    goal_yaw_rad: float | None = None


def propose_goal(
    mode: PersonMode,
    robot_x_m: float,
    robot_y_m: float,
    person_x_m: float,
    person_y_m: float,
    config: PolicyConfig,
) -> GoalDecision:
    config.validate()
    dx = person_x_m - robot_x_m
    dy = person_y_m - robot_y_m
    distance = math.hypot(dx, dy)
    if not math.isfinite(distance) or distance < 1e-6:
        return GoalDecision("hold", "degenerate_target", distance)

    if mode == PersonMode.SUMMON:
        lower = config.summon_min_distance_m
        upper = config.summon_arrival_distance_m
        standoff = config.summon_distance_m
        arrived_action = "arrived"
    elif mode == PersonMode.FOLLOW:
        lower = config.follow_min_distance_m
        upper = config.follow_hold_distance_m
        standoff = config.follow_distance_m
        arrived_action = "hold"
    else:
        return GoalDecision("reject", "unsupported_mode", distance)

    if distance < lower:
        return GoalDecision("hold", "person_too_close_no_reverse", distance)
    if distance <= upper:
        return GoalDecision(arrived_action, "inside_holding_band", distance)

    unit_x = dx / distance
    unit_y = dy / distance
    return GoalDecision(
        "navigate",
        "person_outside_holding_band",
        distance,
        person_x_m - standoff * unit_x,
        person_y_m - standoff * unit_y,
        math.atan2(dy, dx),
    )


class GoalThrottler:
    """Bound rolling Nav2 goal replacement by displacement and elapsed time."""

    def __init__(self, displacement_m: float = 0.20, refresh_sec: float = 0.75) -> None:
        self.displacement_m = displacement_m
        self.refresh_ns = int(refresh_sec * 1e9)
        self._last_target: tuple[float, float] | None = None
        self._last_time_ns: int | None = None

    def should_replace(self, target_x_m: float, target_y_m: float, now_ns: int) -> bool:
        if self._last_target is None or self._last_time_ns is None:
            return True
        displacement = math.hypot(target_x_m - self._last_target[0], target_y_m - self._last_target[1])
        return displacement >= self.displacement_m or now_ns - self._last_time_ns >= self.refresh_ns

    def mark_submitted(self, target_x_m: float, target_y_m: float, now_ns: int) -> None:
        self._last_target = (target_x_m, target_y_m)
        self._last_time_ns = now_ns


def should_submit_nav_goal(
    mode: PersonMode,
    goal_already_submitted: bool,
    throttler: GoalThrottler,
    target_x_m: float,
    target_y_m: float,
    now_ns: int,
) -> bool:
    """Submit summon once; reserve rolling replacement for follow mode."""
    if mode == PersonMode.SUMMON:
        return not goal_already_submitted
    if mode == PersonMode.FOLLOW:
        return throttler.should_replace(target_x_m, target_y_m, now_ns)
    return False


def target_speed_mps(
    previous_x_m: float,
    previous_y_m: float,
    previous_time_ns: int,
    current_x_m: float,
    current_y_m: float,
    current_time_ns: int,
) -> float:
    elapsed_ns = current_time_ns - previous_time_ns
    if elapsed_ns <= 0:
        raise ValueError("target_time_not_increasing")
    return math.hypot(current_x_m - previous_x_m, current_y_m - previous_y_m) / (elapsed_ns / 1e9)
