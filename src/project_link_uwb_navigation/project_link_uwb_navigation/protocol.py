"""Strict BU04 JSON payload validation and normalization."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Mapping


class PayloadRejected(ValueError):
    """A payload failed a stable validation rule."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ProtocolConfig:
    tag_address: str
    source_id: str = "tag-1"
    max_coordinate_m: float = 30.0
    max_range_m: float = 30.0
    max_range_residual_m: float = 0.50


@dataclass(frozen=True)
class UwbSample:
    source_id: str
    tag_time_raw: int
    receive_time_ns: int
    x_m: float
    y_m: float
    range_m: float
    raw: Mapping[str, int | str]

    @property
    def coordinate_range_m(self) -> float:
        return math.hypot(self.x_m, self.y_m)

    @property
    def range_residual_m(self) -> float:
        return abs(self.coordinate_range_m - self.range_m)

    @property
    def debug_bearing_rad(self) -> float:
        return math.atan2(self.x_m, self.y_m)


class TagClockGuard:
    """Accept only strictly increasing tag timestamps."""

    def __init__(self) -> None:
        self._last: int | None = None

    def accept(self, tag_time_raw: int) -> bool:
        if self._last is not None and tag_time_raw <= self._last:
            return False
        self._last = tag_time_raw
        return True


def _required_integer(mapping: Mapping[str, object], name: str) -> int:
    value = mapping.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise PayloadRejected(f"invalid_{name}")
    return value


def parse_payload(payload: bytes, receive_time_ns: int, config: ProtocolConfig) -> UwbSample:
    """Parse one decoded JSON payload into SI units."""

    if not config.tag_address.strip():
        raise PayloadRejected("tag_address_not_configured")
    try:
        decoded = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PayloadRejected("invalid_utf8") from exc
    try:
        root = json.loads(decoded)
    except json.JSONDecodeError as exc:
        raise PayloadRejected("invalid_json") from exc
    if not isinstance(root, dict) or not isinstance(root.get("TWR"), dict):
        raise PayloadRejected("missing_TWR")
    twr = root["TWR"]
    address = twr.get("a16")
    if not isinstance(address, str):
        raise PayloadRejected("invalid_a16")
    if address.strip().upper() != config.tag_address.strip().upper():
        raise PayloadRejected("wrong_tag")

    tag_time = _required_integer(twr, "T")
    distance_cm = _required_integer(twr, "D")
    x_cm = _required_integer(twr, "Xcm")
    y_cm = _required_integer(twr, "Ycm")
    if distance_cm < 0:
        raise PayloadRejected("negative_distance")

    x_m = x_cm / 100.0
    y_m = y_cm / 100.0
    range_m = distance_cm / 100.0
    if not all(math.isfinite(value) for value in (x_m, y_m, range_m)):
        raise PayloadRejected("non_finite_value")
    if abs(x_m) > config.max_coordinate_m or abs(y_m) > config.max_coordinate_m:
        raise PayloadRejected("coordinate_out_of_bounds")
    if range_m > config.max_range_m:
        raise PayloadRejected("range_out_of_bounds")

    retained: dict[str, int | str] = {"a16": address}
    for name in ("R", "P", "O", "V", "X", "Y", "Z"):
        if name not in twr:
            continue
        value = twr[name]
        if isinstance(value, bool) or not isinstance(value, int):
            raise PayloadRejected(f"invalid_{name}")
        retained[name] = value
    sample = UwbSample(
        source_id=config.source_id,
        tag_time_raw=tag_time,
        receive_time_ns=receive_time_ns,
        x_m=x_m,
        y_m=y_m,
        range_m=range_m,
        raw=retained,
    )
    if sample.range_residual_m > config.max_range_residual_m:
        raise PayloadRejected("range_residual_too_large")
    return sample
