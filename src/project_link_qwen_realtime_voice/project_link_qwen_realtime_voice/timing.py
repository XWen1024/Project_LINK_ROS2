"""Private JSONL timing diagnostics for realtime voice turns."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


class TimingTrace:
    def __init__(self, path: str, logger, source: str = "qwen_realtime") -> None:
        self.trace_id = uuid.uuid4().hex[:12]
        self._path = Path(path).expanduser()
        self._logger = logger
        self._source = source
        self._started = time.perf_counter()
        self._last = self._started
        self._lock = threading.Lock()

    def event(self, phase: str, **fields: Any) -> None:
        now = time.perf_counter()
        with self._lock:
            payload = {
                "timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                "kind": "timing",
                "trace_id": self.trace_id,
                "source": self._source,
                "phase": phase,
                "elapsed_ms": round((now - self._started) * 1000.0, 3),
                "delta_ms": round((now - self._last) * 1000.0, 3),
                **fields,
            }
            self._last = now
            line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            self._logger.info("[QWEN_TIMING] " + line)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
            try:
                os.chmod(self._path, 0o600)
            except OSError:
                pass
