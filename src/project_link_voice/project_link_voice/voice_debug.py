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
        phase_elapsed_ms = round(float(elapsed_ms), 3)
        payload = self._payload(
            "timing",
            trace_id,
            "phase",
            {
                "phase": phase,
                "elapsed_ms": phase_elapsed_ms,
                "phase_elapsed_ms": phase_elapsed_ms,
                **fields,
            },
        )
        self._write(self._timing_path, payload)
        if self._timing_console_enabled:
            self._ros_logger.info(self._format_timing_console(payload))

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
            self._ros_logger.info(
                f"[VOICE_TIMING] {payload['timestamp']} +0.000ms "
                f"total={payload['total_ms']:.3f}ms trace={trace_id} phase=summary"
            )

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

    @staticmethod
    def _format_timing_console(payload: dict[str, Any]) -> str:
        step_delta_ms = float(payload.get("step_delta_ms", payload["elapsed_ms"]))
        trace_total_ms = float(payload.get("trace_total_ms", payload["elapsed_ms"]))
        derived = " metric=derived" if payload.get("derived") else ""
        return (
            f"[VOICE_TIMING] {payload['timestamp']} +{step_delta_ms:.3f}ms "
            f"total={trace_total_ms:.3f}ms trace={payload['trace_id']} "
            f"phase={payload['phase']} phase_elapsed={payload['phase_elapsed_ms']:.3f}ms"
            f"{derived}"
        )

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
        self._last_timeline_at = self._started_at
        self._phases: dict[str, float] = {}
        self._lock = threading.Lock()
        self._completed = False
        self._completion_fields: dict[str, Any] = {}
        self._phase_events: dict[str, threading.Event] = {}
        self._phase_marks: dict[str, float] = {}
        self._references: dict[str, float] = {}

    def debug(self, event: str, **fields: Any) -> None:
        self._sink.debug(self.trace_id, event, source=self.source, **fields)

    def record(self, phase: str, elapsed_ms: float, **fields: Any) -> None:
        value = float(elapsed_ms)
        derived: list[tuple[str, float]] = []
        with self._lock:
            now = time.perf_counter()
            step_delta_ms = (now - self._last_timeline_at) * 1000.0
            trace_total_ms = (now - self._started_at) * 1000.0
            self._last_timeline_at = now
            self._phases[phase] = self._phases.get(phase, 0.0) + value
            self._phase_marks[phase] = now
            if phase == "asr_final" and "vad_terminal" in self._references:
                derived.append(("vad_to_asr_final", (now - self._references["vad_terminal"]) * 1000.0))
            elif phase == "llm_request_sent" and "asr_final" in self._phase_marks:
                derived.append(("asr_final_to_llm_send", (now - self._phase_marks["asr_final"]) * 1000.0))
            elif phase == "llm_tool_call_complete" and "llm_request_sent" in self._phase_marks:
                derived.append(("llm_to_tool_call", (now - self._phase_marks["llm_request_sent"]) * 1000.0))
            elif phase == "tts_request_sent" and "python_tool" in self._phase_marks:
                derived.append(("tool_to_tts_send", (now - self._phase_marks["python_tool"]) * 1000.0))
            elif phase == "tts_first_audio" and "tts_request_sent" in self._phase_marks:
                derived.append(("tts_to_first_audio", (now - self._phase_marks["tts_request_sent"]) * 1000.0))
            elif phase == "tts_playback_started":
                if "tts_first_audio" in self._phase_marks:
                    derived.append(("first_audio_to_playback", (now - self._phase_marks["tts_first_audio"]) * 1000.0))
                if "speech_end_estimated" in self._references:
                    derived.append(
                        ("speech_end_to_first_playback", (now - self._references["speech_end_estimated"]) * 1000.0)
                    )
            for derived_phase, derived_ms in derived:
                self._phases[derived_phase] = self._phases.get(derived_phase, 0.0) + derived_ms
                self._phase_marks[derived_phase] = now
            emit_late_summary = self._completed and phase in (
                "tts_playback_started",
                "tts_synthesis_complete",
                "tts_playback_complete",
            )
            phases = dict(self._phases) if emit_late_summary else {}
            completion_fields = dict(self._completion_fields) if emit_late_summary else {}
        self._sink.timing(
            self.trace_id,
            phase,
            value,
            source=self.source,
            step_delta_ms=round(step_delta_ms, 3),
            trace_total_ms=round(trace_total_ms, 3),
            **fields,
        )
        for derived_phase, derived_ms in derived:
            self._sink.timing(
                self.trace_id,
                derived_phase,
                derived_ms,
                source=self.source,
                derived=True,
                step_delta_ms=0.0,
                trace_total_ms=round(trace_total_ms, 3),
            )
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
        with self._lock:
            event = self._phase_events.setdefault(phase, threading.Event())
            event.set()
        self.record(phase, elapsed_ms, **(fields or {}))

    def wait_for_phase(self, phase: str, timeout_sec: float) -> bool:
        with self._lock:
            event = self._phase_events.setdefault(phase, threading.Event())
        return event.wait(max(0.0, timeout_sec))

    def mark_reference(self, name: str, timestamp: float | None = None) -> None:
        with self._lock:
            self._references[name] = time.perf_counter() if timestamp is None else float(timestamp)

    @property
    def completed(self) -> bool:
        with self._lock:
            return self._completed

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
