"""Framed local IPC client for the native Volcengine WebSocket bridge."""

from __future__ import annotations

import json
import os
import socket
import struct
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


PROTOCOL_VERSION = 1
HEADER = struct.Struct("!BBHIQ")
MAX_FRAME_BYTES = 16 * 1024 * 1024

CMD_AUDIO = 1
CMD_COMMIT = 2
CMD_CLEAR = 3
CMD_INTERRUPT = 4
CMD_RAW_JSON = 5
CMD_PING = 6
CMD_SHUTDOWN = 7

EVT_CONTROL = 129
EVT_MESSAGE = 130
EVT_AUDIO = 131


@dataclass(frozen=True)
class BridgeFrame:
    message_type: int
    flags: int
    monotonic_ns: int
    payload: bytes


def encode_frame(
    message_type: int,
    payload: bytes = b"",
    *,
    flags: int = 0,
    monotonic_ns: int | None = None,
) -> bytes:
    body = bytes(payload)
    if len(body) > MAX_FRAME_BYTES:
        raise ValueError("bridge frame is too large")
    timestamp = time.monotonic_ns() if monotonic_ns is None else int(monotonic_ns)
    return HEADER.pack(
        int(message_type),
        PROTOCOL_VERSION,
        int(flags),
        len(body),
        timestamp,
    ) + body


def _read_exact(sock: socket.socket, length: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < length:
        chunk = sock.recv(length - len(chunks))
        if not chunk:
            raise EOFError("native bridge IPC closed")
        chunks.extend(chunk)
    return bytes(chunks)


def read_frame(sock: socket.socket) -> BridgeFrame:
    header = _read_exact(sock, HEADER.size)
    message_type, version, flags, length, timestamp = HEADER.unpack(header)
    if version != PROTOCOL_VERSION:
        raise RuntimeError(f"unsupported bridge protocol version: {version}")
    if length > MAX_FRAME_BYTES:
        raise RuntimeError(f"bridge frame exceeds limit: {length}")
    return BridgeFrame(message_type, flags, timestamp, _read_exact(sock, length))


class VolcS2SBridgeProcess:
    def __init__(
        self,
        executable: str,
        event_callback: Callable[[BridgeFrame], None],
        log_callback: Callable[[str], None] | None = None,
    ) -> None:
        self._executable = str(Path(executable).expanduser())
        self._event_callback = event_callback
        self._log_callback = log_callback
        self._socket: socket.socket | None = None
        self._process: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._send_lock = threading.Lock()
        self._stop = threading.Event()
        self._connected = threading.Event()
        self._last_control: dict[str, Any] = {}

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    @property
    def last_control(self) -> dict[str, Any]:
        return dict(self._last_control)

    def start(self) -> None:
        if self._process is not None:
            return
        if os.name != "posix":
            raise RuntimeError("Volcengine native bridge requires Linux POSIX file descriptors")
        if not os.path.isfile(self._executable) or not os.access(self._executable, os.X_OK):
            raise RuntimeError(f"native bridge executable is missing or not executable: {self._executable}")
        parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        child.set_inheritable(True)
        try:
            self._process = subprocess.Popen(
                [self._executable, "--ipc-fd", str(child.fileno())],
                pass_fds=(child.fileno(),),
                close_fds=True,
                start_new_session=True,
            )
        finally:
            child.close()
        self._socket = parent
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name="volc-s2s-bridge-reader",
            daemon=True,
        )
        self._reader_thread.start()

    def wait_connected(self, timeout_sec: float) -> bool:
        return self._connected.wait(max(0.0, float(timeout_sec)))

    def send_audio(self, pcm: bytes) -> int:
        return self._send(CMD_AUDIO, pcm)

    def commit(self) -> int:
        return self._send(CMD_COMMIT)

    def clear(self) -> int:
        return self._send(CMD_CLEAR)

    def interrupt(self) -> int:
        return self._send(CMD_INTERRUPT)

    def ping(self) -> int:
        return self._send(CMD_PING)

    def send_json(self, value: dict[str, Any] | str) -> int:
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return self._send(CMD_RAW_JSON, text.encode("utf-8"))

    def _send(self, message_type: int, payload: bytes = b"") -> int:
        sock = self._socket
        if sock is None or self._stop.is_set():
            raise RuntimeError("native bridge is not running")
        timestamp = time.monotonic_ns()
        packet = encode_frame(message_type, payload, monotonic_ns=timestamp)
        with self._send_lock:
            sock.sendall(packet)
        return timestamp

    def _reader_loop(self) -> None:
        try:
            while not self._stop.is_set():
                sock = self._socket
                if sock is None:
                    break
                frame = read_frame(sock)
                if frame.message_type == EVT_CONTROL:
                    self._handle_control(frame.payload)
                self._event_callback(frame)
        except (EOFError, OSError, RuntimeError) as exc:
            if not self._stop.is_set() and self._log_callback is not None:
                self._log_callback(f"Volcengine native bridge reader stopped: {exc}")
        finally:
            self._connected.clear()

    def _handle_control(self, payload: bytes) -> None:
        try:
            control = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(control, dict):
            return
        self._last_control = control
        if control.get("event") == "sdk_event":
            if control.get("connected") is True:
                self._connected.set()
            else:
                self._connected.clear()

    def close(self, timeout_sec: float = 4.0) -> None:
        if self._stop.is_set():
            return
        try:
            self._send(CMD_SHUTDOWN)
        except (OSError, RuntimeError):
            pass
        self._stop.set()
        sock = self._socket
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
            self._socket = None
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=1.0)
        process = self._process
        if process is not None:
            try:
                process.wait(timeout=max(0.1, timeout_sec))
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1.0)
        self._process = None

