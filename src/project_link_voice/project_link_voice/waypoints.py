"""Waypoint persistence and exact named-location matching."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class Waypoint:
    name: str
    x: float
    y: float
    yaw: float


class WaypointStore:
    """Loads packaged defaults and an optional user-owned JSON override."""

    def __init__(self, default_path: Path, override_path: Path | None = None) -> None:
        self._default_path = Path(default_path)
        self._override_path = Path(override_path) if override_path else None
        self._waypoints = self._load()

    def _read_file(self, path: Path) -> dict[str, dict[str, float]]:
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as stream:
            data = json.load(stream)
        if not isinstance(data, dict):
            raise ValueError(f"Waypoint file must contain an object: {path}")
        return data

    def _load(self) -> dict[str, Waypoint]:
        merged = self._read_file(self._default_path)
        if self._override_path:
            merged.update(self._read_file(self._override_path))
        return {
            name: Waypoint(name, float(value["x"]), float(value["y"]), float(value.get("yaw", 0.0)))
            for name, value in merged.items()
        }

    def names(self) -> list[str]:
        return sorted(self._waypoints)

    def save(self, name: str, x: float, y: float, yaw: float) -> None:
        if not self._override_path:
            raise ValueError("waypoints_override_file is required to save waypoints")
        self._override_path.parent.mkdir(parents=True, exist_ok=True)
        data = self._read_file(self._override_path)
        data[name] = {"x": float(x), "y": float(y), "yaw": float(yaw)}
        with self._override_path.open("w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        self._waypoints[name] = Waypoint(name, float(x), float(y), float(yaw))

    def find_in_text(self, text: str) -> Waypoint | None:
        matches = [waypoint for name, waypoint in self._waypoints.items() if name in text]
        if not matches:
            return None
        return max(matches, key=lambda waypoint: len(waypoint.name))

    def get(self, name: str) -> Waypoint | None:
        return self._waypoints.get(name)


CONFIRM_WORDS = ("确认", "确定", "前往", "开始", "是的", "好的")
CANCEL_WORDS = ("停止", "取消", "急停", "不要了", "算了")


def contains_any(text: str, words: tuple[str, ...]) -> bool:
    return any(word in text for word in words)


@dataclass
class ConfirmationState:
    pending_waypoint: Waypoint | None = None
    driving: bool = False

    def consume(self, text: str, store: WaypointStore) -> tuple[str, Waypoint | None]:
        """Return a local command: target, confirm, cancel, or unknown."""
        normalized = text.strip()
        if contains_any(normalized, CANCEL_WORDS):
            self.pending_waypoint = None
            return "cancel", None
        if self.pending_waypoint and contains_any(normalized, CONFIRM_WORDS):
            waypoint = self.pending_waypoint
            self.pending_waypoint = None
            self.driving = True
            return "confirm", waypoint
        waypoint = store.find_in_text(normalized)
        if waypoint:
            self.pending_waypoint = waypoint
            return "target", waypoint
        return "unknown", None

    def clear_drive(self) -> None:
        self.driving = False
