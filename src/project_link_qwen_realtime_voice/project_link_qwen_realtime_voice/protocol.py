"""Provider-neutral realtime events and helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RealtimeEvent:
    type: str
    payload: dict[str, Any]


def event_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    output = getattr(value, "output", None)
    if isinstance(output, dict):
        return output
    if hasattr(value, "to_dict"):
        converted = value.to_dict()
        if isinstance(converted, dict):
            return converted
    data = getattr(value, "__dict__", None)
    return dict(data) if isinstance(data, dict) else {"value": value}


def normalize_event(value: Any) -> RealtimeEvent:
    payload = event_dict(value)
    event_type = str(payload.get("type") or payload.get("event") or "unknown")
    return RealtimeEvent(event_type, payload)


def nested_text(payload: dict[str, Any], *keys: str) -> str:
    value: Any = payload
    for key in keys:
        if not isinstance(value, dict):
            return ""
        value = value.get(key)
    return value if isinstance(value, str) else ""
