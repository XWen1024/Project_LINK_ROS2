"""Asynchronous SSH client for the allowlisted Orin configuration helper."""

from __future__ import annotations

from collections import deque
import json
from typing import Any

from PySide6.QtCore import QObject, QProcess, QSettings, Signal


class ConfigClient(QObject):
    loaded = Signal(str, dict)
    saved = Signal(str, dict)
    failed = Signal(str, str)
    busy_changed = Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._settings = QSettings("ProjectLINK", "ControlConsole")
        self._queue: deque[tuple[str, str, dict[str, Any] | None]] = deque()
        self._process: QProcess | None = None
        self._current: tuple[str, str, dict[str, Any] | None] | None = None
        self._stdout = bytearray()
        self._stderr = bytearray()

    @property
    def ssh_target(self) -> str:
        return str(self._settings.value("orin/ssh_target", "wte@orin"))

    @property
    def workspace(self) -> str:
        return str(self._settings.value("orin/workspace", "/home/wte/wheeltec_robot"))

    def set_connection(self, ssh_target: str, workspace: str) -> None:
        target = ssh_target.strip() or "wte@orin"
        root = workspace.strip() or "/home/wte/wheeltec_robot"
        self._settings.setValue("orin/ssh_target", target)
        self._settings.setValue("orin/workspace", root)

    def load(self, section: str) -> None:
        self._enqueue("get", section, None)

    def save(self, section: str, payload: dict[str, Any]) -> None:
        self._enqueue("set", section, payload)

    def _enqueue(self, operation: str, section: str, payload: dict[str, Any] | None) -> None:
        if section not in {"voice", "global", "uwb"}:
            self.failed.emit(section, "不支持的配置分区")
            return
        self._queue.append((operation, section, payload))
        if self._process is None:
            self._start_next()

    def _start_next(self) -> None:
        if not self._queue:
            self.busy_changed.emit(False)
            return
        self.busy_changed.emit(True)
        self._current = self._queue.popleft()
        operation, section, payload = self._current
        helper = self.workspace.rstrip("/") + "/scripts/project_link_console_config.py"
        process = QProcess(self)
        process.setProgram("ssh")
        process.setArguments(
            [
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=8",
                self.ssh_target,
                "python3",
                helper,
                operation,
                section,
            ]
        )
        process.readyReadStandardOutput.connect(self._read_stdout)
        process.readyReadStandardError.connect(self._read_stderr)
        process.finished.connect(self._finished)
        process.errorOccurred.connect(self._process_error)
        self._stdout.clear()
        self._stderr.clear()
        self._process = process
        process.start()
        if payload is not None:
            process.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        process.closeWriteChannel()

    def _read_stdout(self) -> None:
        if self._process is not None:
            self._stdout.extend(bytes(self._process.readAllStandardOutput()))

    def _read_stderr(self) -> None:
        if self._process is not None:
            self._stderr.extend(bytes(self._process.readAllStandardError()))

    def _process_error(self, _error) -> None:
        if self._process is not None:
            self._read_stderr()

    def _finished(self, exit_code: int, _exit_status) -> None:
        process = self._process
        current = self._current
        if process is not None:
            self._read_stdout()
            self._read_stderr()
            process.deleteLater()
        self._process = None
        self._current = None
        if current is None:
            self._start_next()
            return
        operation, section, _payload = current
        output = self._stdout.decode("utf-8", errors="replace").strip()
        error = self._stderr.decode("utf-8", errors="replace").strip()
        if exit_code != 0:
            self.failed.emit(section, error or output or f"SSH 配置命令失败：{exit_code}")
        else:
            try:
                value = json.loads(output or "{}")
            except json.JSONDecodeError as exc:
                self.failed.emit(section, f"配置响应不是有效 JSON：{exc}")
            else:
                if operation == "get":
                    self.loaded.emit(section, value)
                else:
                    self.saved.emit(section, value)
        self._start_next()

    def shutdown(self) -> None:
        self._queue.clear()
        if self._process is not None:
            self._process.kill()
            self._process.waitForFinished(1000)
            self._process = None
