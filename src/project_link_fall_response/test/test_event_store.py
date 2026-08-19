import uuid

import pytest

from project_link_fall_response.event_store import BusyEventError, EventStore


def event(mode="demo", event_id=None):
    return {
        "event_id": event_id or str(uuid.uuid4()),
        "mode": mode,
        "occurred_at_ms": 1000,
        "device_name": "phone",
        "cancel_window_ms": 15000,
        "imu": None if mode == "demo" else {
            "peak_accel_g": 2.8,
            "orientation_change_deg": 63.0,
            "inactivity_ms": 2200,
        },
    }


def test_create_is_idempotent(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    payload = event()
    first = store.create_event(payload, received_at_ms=100)
    second = store.create_event(payload, received_at_ms=200)
    assert first.created is True
    assert second.created is False
    assert second.event["received_at_ms"] == 100


def test_real_preempts_unclaimed_demo(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    demo = store.create_event(event("demo"), received_at_ms=100).event
    real = store.create_event(event("real"), received_at_ms=200)
    assert real.preempted_event_id == demo["event_id"]
    assert store.get(demo["event_id"])["status"] == "cancelled"


def test_active_real_rejects_second_event(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    store.create_event(event("real"), received_at_ms=100)
    with pytest.raises(BusyEventError):
        store.create_event(event("demo"), received_at_ms=200)


def test_cancel_and_notification_claim_are_atomic(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    created = store.create_event(event(), received_at_ms=100).event
    store.update(created["event_id"], status="verifying")
    before, claimed = store.claim_notification(created["event_id"], at_ms=15099)
    assert claimed is False
    assert before["notification_claimed_at_ms"] is None
    cancelled, success = store.cancel(created["event_id"])
    assert success is True
    assert cancelled["status"] == "cancelled"
    _, claimed = store.claim_notification(created["event_id"], at_ms=15100)
    assert claimed is False


def test_claim_prevents_late_cancel(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    created = store.create_event(event(), received_at_ms=100).event
    store.update(created["event_id"], status="verifying")
    claimed_event, claimed = store.claim_notification(created["event_id"], at_ms=15100)
    assert claimed is True
    after, cancelled = store.cancel(created["event_id"])
    assert cancelled is False
    assert after["notification_claimed_at_ms"] == claimed_event["notification_claimed_at_ms"]


def test_restart_marks_nonterminal_failed(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    accepted = store.create_event(event(), received_at_ms=100).event
    assert store.recover_incomplete() == 1
    assert store.get(accepted["event_id"])["status"] == "failed"
    assert store.recover_incomplete() == 0
