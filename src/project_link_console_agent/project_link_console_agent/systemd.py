"""Allowlisted systemd user-unit control with injectable command execution."""

from __future__ import annotations

from dataclasses import dataclass
import subprocess
from typing import Callable, Iterable, Sequence


UNITS = {
    "agent": "project-link-console-agent.service",
    "base": "project-link-base.service",
    "lidar": "project-link-lidar.service",
    "front_camera": "project-link-front-camera.service",
    "fall_response": "project-link-fall-response.service",
    "wechatbot": "project-link-wechatbot.service",
    "robot_description": "project-link-robot-description.service",
    "scan": "project-link-scan.service",
    "point_lio_map": "project-link-point-lio-map.service",
    "nav2": "project-link-nav2.service",
    "rf2o": "project-link-rf2o-fallback.service",
    "visual_grasp": "project-link-visual-grasp.service",
    "visual_grasp_detector": "project-link-visual-grasp-detector.service",
    "vl53l0x": "project-link-vl53l0x.service",
    "voice_classic": "project-link-voice-classic.service",
    "voice_qwen": "project-link-voice-qwen.service",
    "uwb_shadow": "project-link-uwb-shadow.service",
    "platform_target": "project-link-platform.target",
    "mapping_target": "project-link-mapping.target",
    "navigation_target": "project-link-navigation.target",
    "rf2o_target": "project-link-rf2o-fallback.target",
    "emergency_target": "project-link-emergency.target",
}

ALLOWED_UNITS = frozenset(UNITS.values())


@dataclass(frozen=True)
class UnitState:
    unit: str
    active_state: str = "unknown"
    sub_state: str = "unknown"
    result: str = "unknown"
    active_enter_timestamp: str = ""
    restart_count: int = 0
    description: str = ""

    @property
    def active(self) -> bool:
        return self.active_state == "active"


Runner = Callable[[Sequence[str], float], subprocess.CompletedProcess[str]]


def _default_runner(command: Sequence[str], timeout_sec: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_sec,
    )


class SystemdManager:
    def __init__(self, command: str = "systemctl", runner: Runner | None = None) -> None:
        self.command = command
        self._runner = runner or _default_runner

    @staticmethod
    def validate_unit(unit: str) -> str:
        if unit not in ALLOWED_UNITS:
            raise ValueError(f"unit_not_allowed:{unit}")
        return unit

    def _run(self, arguments: Sequence[str], timeout_sec: float = 20.0) -> subprocess.CompletedProcess[str]:
        result = self._runner([self.command, "--user", *arguments], timeout_sec)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "systemctl_failed").strip()
            raise RuntimeError(detail)
        return result

    def start(self, unit: str) -> None:
        self._run(["start", self.validate_unit(unit)], timeout_sec=240.0)

    def start_no_block(self, unit: str) -> None:
        self._run(["--no-block", "start", self.validate_unit(unit)], timeout_sec=20.0)

    def stop(self, unit: str) -> None:
        self._run(["stop", self.validate_unit(unit)], timeout_sec=30.0)

    def stop_no_block(self, unit: str) -> None:
        self._run(["--no-block", "stop", self.validate_unit(unit)], timeout_sec=20.0)

    def reset_failed(self, unit: str) -> None:
        self._run(["reset-failed", self.validate_unit(unit)], timeout_sec=10.0)

    def restart(self, unit: str) -> None:
        self._run(["restart", self.validate_unit(unit)], timeout_sec=240.0)

    def restart_no_block(self, unit: str) -> None:
        self._run(["--no-block", "restart", self.validate_unit(unit)], timeout_sec=20.0)

    @staticmethod
    def _state_from_values(unit: str, values: dict[str, str]) -> UnitState:
        try:
            restart_count = int(values.get("NRestarts", "0"))
        except ValueError:
            restart_count = 0
        return UnitState(
            unit=unit,
            active_state=values.get("ActiveState", "unknown"),
            sub_state=values.get("SubState", "unknown"),
            result=values.get("Result", "unknown"),
            active_enter_timestamp=values.get("ActiveEnterTimestamp", ""),
            restart_count=restart_count,
            description=values.get("Description", unit),
        )

    def states(self, units: Iterable[str]) -> dict[str, UnitState]:
        requested = [self.validate_unit(unit) for unit in units]
        if not requested:
            return {}
        result = self._run(
            [
                "show",
                *requested,
                "--no-pager",
                "--property=Id",
                "--property=ActiveState",
                "--property=SubState",
                "--property=Result",
                "--property=ActiveEnterTimestamp",
                "--property=NRestarts",
                "--property=Description",
            ],
            timeout_sec=1.5,
        )
        parsed: dict[str, UnitState] = {}
        values: dict[str, str] = {}

        def finish_block() -> None:
            if not values:
                return
            unit = values.get("Id", "")
            if unit in requested:
                parsed[unit] = self._state_from_values(unit, values)
            values.clear()

        for line in result.stdout.splitlines():
            if not line.strip():
                finish_block()
                continue
            key, separator, value = line.partition("=")
            if separator:
                if key == "Id" and values:
                    finish_block()
                values[key] = value
        finish_block()
        return {
            unit: parsed.get(unit, UnitState(unit=unit, result="unit_missing_from_systemctl_show"))
            for unit in requested
        }

    def state(self, unit: str) -> UnitState:
        unit = self.validate_unit(unit)
        return self.states([unit])[unit]

    def safe_states(self, units: Iterable[str]) -> dict[str, UnitState]:
        requested = [self.validate_unit(unit) for unit in units]
        try:
            return self.states(requested)
        except (RuntimeError, subprocess.TimeoutExpired, ValueError) as exc:
            return {
                unit: UnitState(unit=unit, active_state="unknown", sub_state="fault", result=str(exc))
                for unit in requested
            }

    def safe_state(self, unit: str) -> UnitState:
        unit = self.validate_unit(unit)
        return self.safe_states([unit])[unit]
