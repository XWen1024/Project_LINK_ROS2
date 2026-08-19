"""Persistent single-contact WeChat notifier built around wechatbot-sdk 0.3.0."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import sqlite3
import time
from typing import Any


@dataclass(frozen=True)
class WeChatNotificationResult:
    attempted: bool
    text_success: bool
    image_success: bool
    receipt: str
    message: str


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)
    path.chmod(0o600)


class BindingStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()

    def load(self) -> dict[str, Any] | None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        if not isinstance(payload, dict) or not payload.get("user_id") or not payload.get("context_token"):
            return None
        return payload

    def save(self, user_id: str, context_token: str) -> None:
        atomic_json(
            self.path,
            {
                "user_id": user_id,
                "context_token": context_token,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )


class NotificationLedger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS notifications (
                    event_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    text_success INTEGER NOT NULL DEFAULT 0,
                    image_success INTEGER NOT NULL DEFAULT 0,
                    receipt TEXT NOT NULL DEFAULT '',
                    message TEXT NOT NULL DEFAULT '',
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL
                )"""
            )
            connection.execute(
                """UPDATE notifications SET status='failed', message='process restarted after notification claim',
                   updated_at_ms=? WHERE status='sending'""",
                (time.time_ns() // 1_000_000,),
            )
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def claim(self, event_id: str) -> tuple[dict[str, Any], bool]:
        timestamp = time.time_ns() // 1_000_000
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM notifications WHERE event_id=?", (event_id,)).fetchone()
            if row is not None:
                connection.commit()
                return dict(row), False
            connection.execute(
                "INSERT INTO notifications(event_id,status,created_at_ms,updated_at_ms) VALUES(?,'sending',?,?)",
                (event_id, timestamp, timestamp),
            )
            row = connection.execute("SELECT * FROM notifications WHERE event_id=?", (event_id,)).fetchone()
            connection.commit()
            return dict(row), True

    def finish(
        self,
        event_id: str,
        *,
        text_success: bool,
        image_success: bool,
        receipt: str,
        message: str,
    ) -> dict[str, Any]:
        timestamp = time.time_ns() // 1_000_000
        status = "sent" if text_success else "failed"
        with self._connection() as connection:
            connection.execute(
                """UPDATE notifications SET status=?, text_success=?, image_success=?, receipt=?, message=?,
                   updated_at_ms=? WHERE event_id=?""",
                (status, int(text_success), int(image_success), receipt, message, timestamp, event_id),
            )
            return dict(connection.execute("SELECT * FROM notifications WHERE event_id=?", (event_id,)).fetchone())


def format_alert(event_id: str, degraded: bool, confidence: float, reason: str, occurred_at_ms: int) -> str:
    occurred = datetime.fromtimestamp(occurred_at_ms / 1000, tz=timezone.utc).astimezone()
    if degraded:
        heading = "Project LINK 疑似跌倒告警（视觉未确认）"
        body = "手机上报疑似跌倒，但机器人视觉未能完成确认，请尽快主动联系并核实。"
    else:
        heading = "Project LINK 跌倒告警"
        body = "机器人视觉复核发现可信跌倒迹象，请尽快主动联系并核实。"
    return (
        f"{heading}\n{body}\n"
        f"时间：{occurred:%Y-%m-%d %H:%M:%S %z}\n"
        f"置信度：{confidence:.2f}\n"
        f"原因：{reason or '无'}\n"
        f"事件 ID：{event_id}"
    )


class PersistentWeChatBot:
    def __init__(self, cred_path: str | Path, binding_path: str | Path, ledger_path: str | Path) -> None:
        self.cred_path = Path(cred_path).expanduser()
        self.binding = BindingStore(binding_path)
        self.ledger = NotificationLedger(ledger_path)
        self.bot: Any = None
        self._started = False

    @property
    def ready(self) -> bool:
        return self._started and self.binding.load() is not None

    async def start(self) -> None:
        from wechatbot import WeChatBot

        if not self.cred_path.is_file():
            raise RuntimeError(f"WeChat credentials are missing: {self.cred_path}")
        self.cred_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.bot = WeChatBot(cred_path=str(self.cred_path))
        if not hasattr(self.bot, "_context_tokens"):
            raise RuntimeError("wechatbot-sdk contract changed: _context_tokens is unavailable")
        await self.bot.login()
        self.cred_path.chmod(0o600)
        binding = self.binding.load()
        if binding:
            self.bot._context_tokens[binding["user_id"]] = binding["context_token"]

        @self.bot.on_message
        async def remember_bound_contact(message: Any) -> None:
            current = self.binding.load()
            if current and current["user_id"] == message.user_id and message._context_token:
                self.binding.save(message.user_id, message._context_token)

        self._started = True

    async def poll(self) -> None:
        if self.bot is None:
            raise RuntimeError("WeChat bot is not started")
        await self.bot.start()

    def stop(self) -> None:
        if self.bot is not None:
            self.bot.stop()

    async def send_alert(
        self,
        *,
        event_id: str,
        degraded: bool,
        confidence: float,
        reason: str,
        occurred_at_ms: int,
        jpeg_data: bytes,
    ) -> WeChatNotificationResult:
        existing, claimed = self.ledger.claim(event_id)
        if not claimed:
            return WeChatNotificationResult(
                attempted=True,
                text_success=bool(existing["text_success"]),
                image_success=bool(existing["image_success"]),
                receipt=str(existing["receipt"]),
                message=f"existing notification result: {existing['status']}",
            )
        binding = self.binding.load()
        if not self.ready or binding is None or self.bot is None:
            row = self.ledger.finish(
                event_id,
                text_success=False,
                image_success=False,
                receipt="",
                message="WeChat bot or bound contact is unavailable",
            )
            return WeChatNotificationResult(True, False, False, "", row["message"])

        text = format_alert(event_id, degraded, confidence, reason, occurred_at_ms)
        text_success = False
        image_success = False
        error_message = ""
        for attempt in range(3):
            try:
                await self.bot.send(binding["user_id"], text)
                text_success = True
                break
            except asyncio.TimeoutError as exc:
                error_message = f"notification result uncertain after timeout: {exc}"
                break
            except Exception as exc:
                error_message = str(exc)
                if attempt < 2:
                    await asyncio.sleep(0.5 * (2**attempt))
        if text_success and jpeg_data:
            try:
                await self.bot.send_media(binding["user_id"], {"image": jpeg_data})
                image_success = True
            except Exception as exc:
                error_message = f"text sent but image failed: {exc}"
        receipt = secrets.token_hex(8) if text_success else ""
        row = self.ledger.finish(
            event_id,
            text_success=text_success,
            image_success=image_success,
            receipt=receipt,
            message=error_message or "sent",
        )
        return WeChatNotificationResult(
            attempted=True,
            text_success=text_success,
            image_success=image_success,
            receipt=receipt,
            message=str(row["message"]),
        )
