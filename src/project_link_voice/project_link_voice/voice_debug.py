"""Structured debug and timing logs for one voice interaction."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator


class VoiceDebugSink:
    def __init__(
        self,
        ros_logger,
        debug_enabled: bool = True,
        timing_enabled: bool = True,
        debug_log_file: str = "~/.ros/project_link_voice/voice_debug.jsonl",
        timing_log_file: str = "~/.ros/project_link_voice/voice_timing.jsonl",
        timing_console_enabled: bool = True,
    ) -> None:
        self._ros_logger = ros_logger
        self._debug_enabled = bool(debug_enabled)
        self._timing_enabled = bool(timing_enabled)
        self._timing_console_enabled = bool(timing_console_enabled)
        self._debug_path = self._resolve_path(debug_log_file) if self._debug_enabled else None
        self._timing_path = self._resolve_path(timing_log_file) if self._timing_enabled else None
        self._lock = threading.Lock()
        self._warned_paths: set[Path] = set()

    def start_trace(self, source: str, **fields: Any) -> "VoiceTrace":
        trace = VoiceTrace(self, source)
        trace.debug("trace_started", **fields)
        return trace

    def debug(self, trace_id: str, event: str, **fields: Any) -> None:
        if not self._debug_enabled:
            return
        payload = self._payload("debug", trace_id, event, fields)
        self._write(self._debug_path, payload)
        self._ros_logger.debug(f"[VOICE_DEBUG] {self._encode(payload)}")

    def timing(self, trace_id: str, phase: str, elapsed_ms: float, **fields: Any) -> None:
        if not self._timing_enabled:
            return
        payload = self._payload(
            "timing",
            trace_id,
            "phase",
            {"phase": phase, "elapsed_ms": round(float(elapsed_ms), 3), **fields},
        )
        self._write(self._timing_path, payload)
        if self._timing_console_enabled:
            self._ros_logger.info(f"[VOICE_TIMING] {self._encode(payload)}")

    def summary(self, trace_id: str, total_ms: float, phases: dict[str, float], **fields: Any) -> None:
        if not self._timing_enabled:
            return
        payload = self._payload(
            "timing_summary",
            trace_id,
            "summary",
            {
                "total_ms": round(float(total_ms), 3),
                "phases_ms": {name: round(value, 3) for name, value in phases.items()},
                **fields,
            },
        )
        self._write(self._timing_path, payload)
        if self._timing_console_enabled:
            self._ros_logger.info(f"[VOICE_TIMING] {self._encode(payload)}")

    @staticmethod
    def _resolve_path(raw_path: str) -> Path | None:
        value = str(raw_path).strip()
        return Path(value).expanduser() if value else None

    @staticmethod
    def _payload(kind: str, trace_id: str, event: str, fields: dict[str, Any]) -> dict[str, Any]:
        return {
            "timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "kind": kind,
            "trace_id": trace_id,
            "event": event,
            **fields,
        }

    @staticmethod
    def _encode(payload: dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)

    def _write(self, path: Path | None, payload: dict[str, Any]) -> None:
        if path is None:
            return
        try:
            with self._lock:
                path.parent.mkdir(parents=True, exist_ok=True)
                file_descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
                with os.fdopen(file_descriptor, "a", encoding="utf-8") as log_file:
                    log_file.write(self._encode(payload) + "\n")
        except OSError as exc:
            if path not in self._warned_paths:
                self._warned_paths.add(path)
                self._ros_logger.warning(f"Voice debug log write failed for {path}: {exc}")


class VoiceTrace:
    def __init__(self, sink: VoiceDebugSink, source: str) -> None:
        self.trace_id = uuid.uuid4().hex[:12]
        self.source = source
        self._sink = sink
        self._started_at = time.perf_counter()
        self._phases: dict[str, float] = {}
        self._marks: dict[str, float] = {}
        self._lock = threading.Lock()
        self._completed = False
        self._completion_fields: dict[str, Any] = {}

    def debug(self, event: str, **fields: Any) -> None:
        self._sink.debug(self.trace_id, event, source=self.source, **fields)

    def record(self, phase: str, elapsed_ms: float, **fields: Any) -> None:
        value = float(elapsed_ms)
        with self._lock:
            self._phases[phase] = self._phases.get(phase, 0.0) + value
            emit_late_summary = self._completed and phase == "tts_synthesis_complete"
            phases = dict(self._phases) if emit_late_summary else {}
            completion_fields = dict(self._completion_fields) if emit_late_summary else {}
        self._sink.timing(self.trace_id, phase, value, source=self.source, **fields)
        if emit_late_summary:
            self._sink.summary(
                self.trace_id,
                (time.perf_counter() - self._started_at) * 1000.0,
                phases,
                source=self.source,
                late_tts_update=True,
                **completion_fields,
            )

    def timing_callback(self, phase: str, elapsed_ms: float, fields: dict[str, Any] | None = None) -> None:
        self.record(phase, elapsed_ms, **(fields or {}))

    def mark(self, name: str, **fields: Any) -> float:
        now = time.perf_counter()
        with self._lock:
            self._marks[name] = now
        self.debug("timing_mark", mark=name, **fields)
        return now

    def mark_at(self, name: str, monotonic_seconds: float, **fields: Any) -> float:
        value = float(monotonic_seconds)
        with self._lock:
            self._marks[name] = value
        self.debug("timing_mark", mark=name, externally_timestamped=True, **fields)
        return value

    def record_since(self, phase: str, start_mark: str, **fields: Any) -> float | None:
        with self._lock:
            started_at = self._marks.get(start_mark)
        if started_at is None:
            return None
        elapsed_ms = (time.perf_counter() - started_at) * 1000.0
        self.record(phase, elapsed_ms, start_mark=start_mark, **fields)
        return elapsed_ms

    def record_between(
        self,
        phase: str,
        start_mark: str,
        end_mark: str,
        **fields: Any,
    ) -> float | None:
        with self._lock:
            started_at = self._marks.get(start_mark)
            ended_at = self._marks.get(end_mark)
        if started_at is None or ended_at is None or ended_at < started_at:
            return None
        elapsed_ms = (ended_at - started_at) * 1000.0
        self.record(
            phase,
            elapsed_ms,
            start_mark=start_mark,
            end_mark=end_mark,
            **fields,
        )
        return elapsed_ms

    @contextmanager
    def phase(self, phase: str, **fields: Any) -> Iterator[None]:
        started_at = time.perf_counter()
        try:
            yield
        except Exception as exc:
            self.record(
                phase,
                (time.perf_counter() - started_at) * 1000.0,
                success=False,
                error_type=type(exc).__name__,
                **fields,
            )
            raise
        else:
            self.record(phase, (time.perf_counter() - started_at) * 1000.0, success=True, **fields)

    def complete(self, outcome: str, **fields: Any) -> None:
        with self._lock:
            if self._completed:
                return
            self._completed = True
            self._completion_fields = {"outcome": outcome, **fields}
            phases = dict(self._phases)
        total_ms = (time.perf_counter() - self._started_at) * 1000.0
        self.debug("trace_completed", outcome=outcome, total_ms=round(total_ms, 3), **fields)
        self._sink.summary(
            self.trace_id,
            total_ms,
            phases,
            source=self.source,
            **self._completion_fields,
        )
