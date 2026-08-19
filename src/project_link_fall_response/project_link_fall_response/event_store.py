"""Durable, fail-closed event state for the Android fall gateway."""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time
from typing import Any


TERMINAL_STATUSES = frozenset({"notified", "not_fall", "cancelled", "failed"})
ACTIVE_STATUSES = frozenset({"accepted", "scanning", "verifying"})
ALL_STATUSES = ACTIVE_STATUSES | TERMINAL_STATUSES
ALLOWED_STATUS_TRANSITIONS = {
    "accepted": frozenset({"accepted", "scanning", "cancelled", "failed"}),
    "scanning": frozenset({"scanning", "verifying", "cancelled", "failed"}),
    "verifying": frozenset(
        {"verifying", "notified", "not_fall", "cancelled", "failed"}
    ),
    "notified": frozenset({"notified"}),
    "not_fall": frozenset({"not_fall"}),
    "cancelled": frozenset({"cancelled"}),
    "failed": frozenset({"failed"}),
}


class BusyEventError(RuntimeError):
    """Raised when another event owns the single processing slot."""


@dataclass(frozen=True)
class CreateResult:
    event: dict[str, Any]
    created: bool
    preempted_event_id: str = ""


def now_ms() -> int:
    return time.time_ns() // 1_000_000


class EventStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL CHECK(mode IN ('real', 'demo')),
                    device_name TEXT NOT NULL,
                    occurred_at_ms INTEGER NOT NULL,
                    imu_json TEXT,
                    received_at_ms INTEGER NOT NULL,
                    notify_not_before_ms INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL DEFAULT 'accepted',
                    message TEXT NOT NULL DEFAULT '',
                    local_confidence REAL NOT NULL DEFAULT 0,
                    vlm_confidence REAL NOT NULL DEFAULT 0,
                    assessment_reason TEXT NOT NULL DEFAULT '',
                    degraded INTEGER NOT NULL DEFAULT 0,
                    degraded_reason TEXT NOT NULL DEFAULT '',
                    ros_goal_id TEXT NOT NULL DEFAULT '',
                    cancel_requested_at_ms INTEGER,
                    notification_claimed_at_ms INTEGER,
                    notification_attempted_at_ms INTEGER,
                    notification_succeeded_at_ms INTEGER,
                    text_success INTEGER,
                    image_success INTEGER,
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS event_transitions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
                    from_status TEXT,
                    to_status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_active
                    ON events(status, received_at_ms);
                """
            )
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    def get(self, event_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            return self._row(connection.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone())

    def active_event(self) -> dict[str, Any] | None:
        placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
        with self._connection() as connection:
            row = connection.execute(
                f"SELECT * FROM events WHERE status IN ({placeholders}) ORDER BY received_at_ms LIMIT 1",
                tuple(ACTIVE_STATUSES),
            ).fetchone()
            return self._row(row)

    def list_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        bounded = max(1, min(200, int(limit)))
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM events ORDER BY received_at_ms DESC LIMIT ?",
                (bounded,),
            ).fetchall()
            return [dict(row) for row in rows]

    def transitions(self, event_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT event_id, from_status, to_status, stage, message, created_at_ms
                   FROM event_transitions WHERE event_id=? ORDER BY id ASC""",
                (event_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def create_event(self, payload: dict[str, Any], received_at_ms: int | None = None) -> CreateResult:
        received = int(received_at_ms if received_at_ms is not None else now_ms())
        event_id = str(payload["event_id"])
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone()
            if existing is not None:
                connection.commit()
                return CreateResult(dict(existing), False)

            placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
            active = connection.execute(
                f"SELECT * FROM events WHERE status IN ({placeholders}) ORDER BY received_at_ms LIMIT 1",
                tuple(ACTIVE_STATUSES),
            ).fetchone()
            preempted = ""
            if active is not None:
                can_preempt = (
                    active["mode"] == "demo"
                    and payload["mode"] == "real"
                    and active["notification_claimed_at_ms"] is None
                )
                if not can_preempt:
                    connection.rollback()
                    raise BusyEventError(active["event_id"])
                preempted = str(active["event_id"])
                connection.execute(
                    """UPDATE events SET status='cancelled', stage='cancelled',
                       message='preempted by a real fall event', cancel_requested_at_ms=?, updated_at_ms=?
                       WHERE event_id=?""",
                    (received, received, preempted),
                )
                self._insert_transition(
                    connection,
                    preempted,
                    str(active["status"]),
                    "cancelled",
                    "cancelled",
                    "preempted by a real fall event",
                    received,
                )

            notify_not_before = received + int(payload["cancel_window_ms"])
            imu_json = json.dumps(payload.get("imu"), separators=(",", ":")) if payload.get("imu") is not None else None
            connection.execute(
                """INSERT INTO events (
                       event_id, mode, device_name, occurred_at_ms, imu_json,
                       received_at_ms, notify_not_before_ms, status, stage, message,
                       created_at_ms, updated_at_ms
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, 'accepted', 'accepted', 'event persisted', ?, ?)""",
                (
                    event_id,
                    payload["mode"],
                    payload["device_name"],
                    int(payload["occurred_at_ms"]),
                    imu_json,
                    received,
                    notify_not_before,
                    received,
                    received,
                ),
            )
            self._insert_transition(connection, event_id, None, "accepted", "accepted", "event persisted", received)
            row = connection.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone()
            connection.commit()
            return CreateResult(dict(row), True, preempted)

    @staticmethod
    def _insert_transition(
        connection: sqlite3.Connection,
        event_id: str,
        from_status: str | None,
        to_status: str,
        stage: str,
        message: str,
        created_at_ms: int,
    ) -> None:
        connection.execute(
            """INSERT INTO event_transitions
               (event_id, from_status, to_status, stage, message, created_at_ms)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (event_id, from_status, to_status, stage, message, created_at_ms),
        )

    def update(
        self,
        event_id: str,
        *,
        status: str | None = None,
        stage: str | None = None,
        message: str | None = None,
        **fields: Any,
    ) -> dict[str, Any] | None:
        timestamp = now_ms()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone()
            if current is None:
                connection.rollback()
                return None
            current_status = str(current["status"])
            target_status = status or current_status
            if current_status in TERMINAL_STATUSES and target_status != current_status:
                connection.commit()
                return dict(current)
            if target_status not in ALL_STATUSES:
                connection.rollback()
                raise ValueError(f"invalid event status: {target_status}")
            if target_status not in ALLOWED_STATUS_TRANSITIONS[current_status]:
                connection.rollback()
                raise ValueError(
                    f"invalid event status transition: {current_status}->{target_status}"
                )
            values: dict[str, Any] = dict(fields)
            values["status"] = target_status
            values["stage"] = stage if stage is not None else str(current["stage"])
            values["message"] = message if message is not None else str(current["message"])
            values["updated_at_ms"] = timestamp
            allowed = {
                "status", "stage", "message", "local_confidence", "vlm_confidence",
                "assessment_reason", "degraded", "degraded_reason", "ros_goal_id",
                "notification_attempted_at_ms", "notification_succeeded_at_ms",
                "text_success", "image_success", "updated_at_ms",
            }
            unknown = set(values) - allowed
            if unknown:
                connection.rollback()
                raise ValueError(f"unsupported event fields: {sorted(unknown)}")
            assignments = ", ".join(f"{key}=?" for key in values)
            connection.execute(
                f"UPDATE events SET {assignments} WHERE event_id=?",
                (*values.values(), event_id),
            )
            if target_status != current_status or values["stage"] != current["stage"]:
                self._insert_transition(
                    connection,
                    event_id,
                    current_status,
                    target_status,
                    str(values["stage"]),
                    str(values["message"]),
                    timestamp,
                )
            row = connection.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone()
            connection.commit()
            return dict(row)

    def cancel(
        self, event_id: str, reason: str = "cancelled by phone"
    ) -> tuple[dict[str, Any] | None, bool]:
        timestamp = now_ms()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone()
            if current is None:
                connection.rollback()
                return None, False
            if current["status"] in TERMINAL_STATUSES or current["notification_claimed_at_ms"] is not None:
                connection.commit()
                return dict(current), False
            connection.execute(
                """UPDATE events SET status='cancelled', stage='cancelled', message=?,
                   cancel_requested_at_ms=?, updated_at_ms=? WHERE event_id=?""",
                (reason, timestamp, timestamp, event_id),
            )
            self._insert_transition(
                connection,
                event_id,
                str(current["status"]),
                "cancelled",
                "cancelled",
                reason,
                timestamp,
            )
            row = connection.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone()
            connection.commit()
            return dict(row), True

    def claim_notification(self, event_id: str, at_ms: int | None = None) -> tuple[dict[str, Any] | None, bool]:
        timestamp = int(at_ms if at_ms is not None else now_ms())
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone()
            if current is None:
                connection.rollback()
                return None, False
            eligible = (
                current["status"] == "verifying"
                and current["notification_claimed_at_ms"] is None
                and timestamp >= int(current["notify_not_before_ms"])
            )
            if eligible:
                connection.execute(
                    "UPDATE events SET notification_claimed_at_ms=?, stage='notifying', updated_at_ms=? WHERE event_id=?",
                    (timestamp, timestamp, event_id),
                )
                self._insert_transition(
                    connection,
                    event_id,
                    str(current["status"]),
                    str(current["status"]),
                    "notifying",
                    "notification send right claimed",
                    timestamp,
                )
            row = connection.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone()
            connection.commit()
            return dict(row), bool(eligible)

    def recover_incomplete(self) -> int:
        timestamp = now_ms()
        placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                f"SELECT event_id, status FROM events WHERE status IN ({placeholders})",
                tuple(ACTIVE_STATUSES),
            ).fetchall()
            for row in rows:
                message = "gateway restarted during processing; event will not be resumed"
                connection.execute(
                    "UPDATE events SET status='failed', stage='recovered_failed', message=?, updated_at_ms=? WHERE event_id=?",
                    (message, timestamp, row["event_id"]),
                )
                self._insert_transition(
                    connection,
                    str(row["event_id"]),
                    str(row["status"]),
                    "failed",
                    "recovered_failed",
                    message,
                    timestamp,
                )
            connection.commit()
            return len(rows)
