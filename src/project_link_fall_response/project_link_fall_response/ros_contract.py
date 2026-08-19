"""Conversion helpers for the typed fall-response operator interface."""

from __future__ import annotations

from builtin_interfaces.msg import Time
from project_link_emergency_interfaces.msg import FallEvent, FallTransition


def time_from_ms(value: int | None) -> Time:
    stamp = Time()
    milliseconds = max(0, int(value or 0))
    stamp.sec = milliseconds // 1000
    stamp.nanosec = (milliseconds % 1000) * 1_000_000
    return stamp


def event_message(row: dict | None) -> FallEvent:
    row = row or {}
    message = FallEvent()
    message.stamp = time_from_ms(row.get("updated_at_ms"))
    message.event_id = str(row.get("event_id", ""))
    message.mode = str(row.get("mode", ""))
    message.device_name = str(row.get("device_name", ""))
    message.occurred_at_ms = int(row.get("occurred_at_ms", 0) or 0)
    message.received_at_ms = int(row.get("received_at_ms", 0) or 0)
    message.notify_not_before_ms = int(row.get("notify_not_before_ms", 0) or 0)
    message.status = str(row.get("status", ""))
    message.stage = str(row.get("stage", ""))
    message.message = str(row.get("message", ""))
    message.local_confidence = float(row.get("local_confidence", 0.0) or 0.0)
    message.vlm_confidence = float(row.get("vlm_confidence", 0.0) or 0.0)
    message.assessment_reason = str(row.get("assessment_reason", ""))
    message.degraded = bool(row.get("degraded", False))
    message.degraded_reason = str(row.get("degraded_reason", ""))
    message.notification_claimed = row.get("notification_claimed_at_ms") is not None
    message.notification_attempted = row.get("notification_attempted_at_ms") is not None
    message.notification_success = row.get("notification_succeeded_at_ms") is not None
    message.text_success = bool(row.get("text_success", False))
    message.image_success = bool(row.get("image_success", False))
    message.updated_at_ms = int(row.get("updated_at_ms", 0) or 0)
    return message


def transition_message(row: dict) -> FallTransition:
    message = FallTransition()
    message.stamp = time_from_ms(row.get("created_at_ms"))
    message.event_id = str(row.get("event_id", ""))
    message.from_status = str(row.get("from_status") or "")
    message.to_status = str(row.get("to_status", ""))
    message.stage = str(row.get("stage", ""))
    message.message = str(row.get("message", ""))
    message.created_at_ms = int(row.get("created_at_ms", 0) or 0)
    return message

