"""Pure teleoperation lease and bounds."""

from __future__ import annotations

from dataclasses import dataclass
import math


def clamp(value: float, limit: float) -> float:
    if not math.isfinite(value):
        return 0.0
    return max(-abs(limit), min(abs(limit), value))


@dataclass
class TeleopLease:
    timeout_sec: float = 0.25
    max_linear_mps: float = 0.18
    max_angular_rps: float = 0.60
    enabled: bool = False
    deadman: bool = False
    linear_x: float = 0.0
    angular_z: float = 0.0
    sequence: int = -1
    last_update_monotonic: float = 0.0

    def update(
        self,
        *,
        enabled: bool,
        deadman: bool,
        linear_x: float,
        angular_z: float,
        sequence: int,
        now: float,
    ) -> None:
        if sequence <= self.sequence:
            return
        self.sequence = sequence
        self.enabled = bool(enabled)
        self.deadman = bool(deadman)
        self.linear_x = clamp(linear_x, self.max_linear_mps)
        self.angular_z = clamp(angular_z, self.max_angular_rps)
        self.last_update_monotonic = float(now)

    def active(self, now: float, mapping_mode: bool, emergency_latched: bool) -> bool:
        return bool(
            mapping_mode
            and not emergency_latched
            and self.enabled
            and self.deadman
            and now - self.last_update_monotonic <= self.timeout_sec
        )

    def clear(self) -> None:
        self.enabled = False
        self.deadman = False
        self.linear_x = 0.0
        self.angular_z = 0.0
