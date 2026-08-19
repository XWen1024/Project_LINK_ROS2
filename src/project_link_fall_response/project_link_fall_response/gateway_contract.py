"""Validation for the Android MVP HTTP contract."""

from __future__ import annotations

import math
import uuid
from typing import Any


class ContractError(ValueError):
    pass


def _number(value: Any, name: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < minimum or result > maximum:
        raise ContractError(f"{name} must be between {minimum} and {maximum}")
    return result


def validate_event(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ContractError("JSON body must be an object")
    required = {"event_id", "mode", "occurred_at_ms", "device_name", "cancel_window_ms", "imu"}
    missing = sorted(required - set(payload))
    if missing:
        raise ContractError(f"missing fields: {', '.join(missing)}")
    try:
        event_id = str(uuid.UUID(str(payload["event_id"])))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ContractError("event_id must be a UUID") from exc
    if event_id != str(payload["event_id"]).lower():
        raise ContractError("event_id must use canonical UUID form")
    mode = payload["mode"]
    if mode not in {"real", "demo"}:
        raise ContractError("mode must be real or demo")
    occurred = payload["occurred_at_ms"]
    if isinstance(occurred, bool) or not isinstance(occurred, int) or occurred < 0:
        raise ContractError("occurred_at_ms must be a non-negative integer")
    device_name = str(payload["device_name"]).strip()
    if not 1 <= len(device_name) <= 64:
        raise ContractError("device_name must contain 1 to 64 characters")
    if payload["cancel_window_ms"] != 15000:
        raise ContractError("cancel_window_ms must equal 15000")
    imu = payload["imu"]
    if mode == "demo":
        if imu is not None:
            raise ContractError("demo mode imu must be null")
        normalized_imu = None
    else:
        if not isinstance(imu, dict):
            raise ContractError("real mode imu must be an object")
        normalized_imu = {
            "peak_accel_g": _number(imu.get("peak_accel_g"), "imu.peak_accel_g", 0.0, 20.0),
            "orientation_change_deg": _number(
                imu.get("orientation_change_deg"), "imu.orientation_change_deg", 0.0, 360.0
            ),
            "inactivity_ms": int(_number(imu.get("inactivity_ms"), "imu.inactivity_ms", 0.0, 60000.0)),
        }
    return {
        "event_id": event_id,
        "mode": mode,
        "occurred_at_ms": int(occurred),
        "device_name": device_name,
        "cancel_window_ms": 15000,
        "imu": normalized_imu,
    }


def public_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": event["event_id"],
        "status": event["status"],
        "stage": event.get("stage", ""),
        "message": event.get("message", ""),
        "updated_at_ms": event.get("updated_at_ms", 0),
    }
