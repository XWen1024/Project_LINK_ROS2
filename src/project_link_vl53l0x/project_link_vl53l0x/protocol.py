"""Pure protocol parsing for the ESP32-C3 VL53L0X text stream."""
from __future__ import annotations

from dataclasses import dataclass


class ProtocolError(ValueError):
    """Raised when a DATA line is present but malformed."""


@dataclass(frozen=True)
class DataFrame:
    sequence: int
    sensor_time_ms: int
    distance_mm: int
    range_status: int


def parse_data_line(line: str) -> DataFrame | None:
    """Parse one line, returning None for logs and non-DATA records."""
    stripped = line.strip()
    if not stripped.startswith("DATA,"):
        return None

    fields = stripped.split(",")
    if len(fields) != 5:
        raise ProtocolError("bad_field_count")

    try:
        values = tuple(int(field) for field in fields[1:])
    except ValueError as exc:
        raise ProtocolError("bad_integer") from exc

    if min(values) < 0:
        raise ProtocolError("negative_field")

    return DataFrame(
        sequence=values[0],
        sensor_time_ms=values[1],
        distance_mm=values[2],
        range_status=values[3],
    )
