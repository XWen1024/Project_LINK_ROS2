"""Windows hardware helpers for the standalone visual grasp lab."""
from __future__ import annotations

from collections import deque
import json
from pathlib import Path
from statistics import median
import threading
import time
from typing import Optional

from project_link_visual_grasp.core import TofReading
from project_link_vl53l0x.protocol import ProtocolError, parse_data_line


def map_display_point_to_frame(
    click_x: float,
    click_y: float,
    display_width: int,
    display_height: int,
    image_width: int,
    image_height: int,
    frame_width: int,
    frame_height: int,
) -> Optional[tuple[int, int]]:
    if min(
        display_width,
        display_height,
        image_width,
        image_height,
        frame_width,
        frame_height,
    ) <= 0:
        return None
    offset_x = (display_width - image_width) / 2.0
    offset_y = (display_height - image_height) / 2.0
    local_x = click_x - offset_x
    local_y = click_y - offset_y
    if not 0.0 <= local_x < image_width or not 0.0 <= local_y < image_height:
        return None
    frame_x = round(local_x * frame_width / image_width)
    frame_y = round(local_y * frame_height / image_height)
    return (
        min(frame_width - 1, max(0, frame_x)),
        min(frame_height - 1, max(0, frame_y)),
    )


class DetailedDebugLogger:
    def __init__(self, directory: Path, enabled: bool) -> None:
        self.directory = directory
        self._enabled = False
        self._path: Optional[Path] = None
        self._file = None
        self._lock = threading.Lock()
        self._sequence = 0
        self.set_enabled(enabled)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def path(self) -> Optional[Path]:
        return self._path

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            if enabled == self._enabled:
                return
            if not enabled:
                self._close_locked()
                self._enabled = False
                return
            self.directory.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S")
            self._path = self.directory / f"visual_grasp_debug_{stamp}.jsonl"
            self._file = self._path.open("a", encoding="utf-8", buffering=1)
            self._enabled = True

    def write(self, event: str, payload: Optional[dict] = None) -> None:
        with self._lock:
            if not self._enabled or self._file is None:
                return
            self._sequence += 1
            record = {
                "sequence": self._sequence,
                "time_local": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "time_unix": time.time(),
                "monotonic_sec": time.monotonic(),
                "thread": threading.current_thread().name,
                "event": event,
                "payload": payload or {},
            }
            self._file.write(
                json.dumps(record, ensure_ascii=False, default=str) + "\n"
            )
            self._file.flush()

    def close(self) -> None:
        with self._lock:
            self._close_locked()
            self._enabled = False

    def _close_locked(self) -> None:
        if self._file is not None:
            self._file.flush()
            self._file.close()
        self._file = None


class CameraCapture:
    def __init__(self) -> None:
        self._capture = None
        self._frame = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.index = -1
        self.width = 0
        self.height = 0
        self.error = ""

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, index: int, width: int, height: int, fps: float) -> tuple[bool, str]:
        self.stop()
        try:
            import cv2

            capture = cv2.VideoCapture(index, cv2.CAP_DSHOW)
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            capture.set(cv2.CAP_PROP_FPS, fps)
            if not capture.isOpened():
                capture.release()
                return False, f"无法打开摄像头索引 {index}"
            self._capture = capture
            self.index = index
            self.width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
            self.height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
            self.error = ""
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
            return True, f"摄像头 {index} 已连接：{self.width}x{self.height}"
        except Exception as exc:
            self.error = str(exc)
            return False, f"摄像头启动失败：{exc}"

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.5)
        self._thread = None
        if self._capture is not None:
            self._capture.release()
        self._capture = None
        with self._lock:
            self._frame = None

    def frame(self):
        with self._lock:
            return None if self._frame is None else self._frame.copy()

    def _loop(self) -> None:
        while not self._stop.is_set() and self._capture is not None:
            ok, frame = self._capture.read()
            if ok and frame is not None:
                with self._lock:
                    self._frame = frame
            else:
                self.error = "摄像头读取失败"
                time.sleep(0.02)


class TofSerialReader:
    def __init__(self) -> None:
        self._port = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._samples: deque[float] = deque(maxlen=5)
        self._last_valid_monotonic: float | None = None
        self._accepted = 0
        self._rejected = 0
        self._rate_times: deque[float] = deque(maxlen=50)
        self.port_name = ""
        self.state = "未连接"
        self.reason = "disconnected"
        self.last_log = ""

    @property
    def connected(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and self._port is not None

    @staticmethod
    def ports() -> list[tuple[str, str]]:
        try:
            from serial.tools import list_ports

            result = []
            for port in list_ports.comports():
                label = f"{port.device} - {port.description}"
                if port.vid is not None and port.pid is not None:
                    label += f" [{port.vid:04x}:{port.pid:04x}]"
                result.append((port.device, label))
            return result
        except Exception:
            return []

    @staticmethod
    def preferred_port() -> str:
        try:
            from serial.tools import list_ports

            ports = list(list_ports.comports())
            for port in ports:
                if port.vid == 0x303A and port.pid == 0x1001:
                    return port.device
            return ports[0].device if ports else ""
        except Exception:
            return ""

    def configure_filter(self, window: int) -> None:
        window = max(1, int(window))
        with self._lock:
            existing = list(self._samples)
            self._samples = deque(existing[-window:], maxlen=window)

    def connect(self, port: str, baudrate: int = 115200) -> tuple[bool, str]:
        self.disconnect()
        if not port:
            return False, "请选择 ToF 串口"
        self.port_name = port
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            args=(port, baudrate),
            daemon=True,
        )
        self._thread.start()
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            if self._port is not None:
                return True, f"ToF 已连接：{port}"
            if self.reason == "serial_error":
                return False, self.last_log
            time.sleep(0.02)
        return True, f"正在连接 ToF：{port}"

    def disconnect(self) -> None:
        self._stop.set()
        if self._port is not None:
            try:
                self._port.close()
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        self._thread = None
        self._port = None
        self.state = "未连接"
        self.reason = "disconnected"

    def reading(self, stale_timeout: float, min_samples: int) -> TofReading:
        with self._lock:
            samples = list(self._samples)
            last_valid = self._last_valid_monotonic
        if last_valid is None:
            return TofReading(None, float("inf"), False, "no_range")
        age = max(0.0, time.monotonic() - last_valid)
        if age > stale_timeout:
            return TofReading(None, age, False, "stale")
        if len(samples) < max(1, min_samples):
            return TofReading(None, age, False, "insufficient_samples")
        return TofReading(float(median(samples)), age, True, "valid")

    def stats(self) -> dict:
        with self._lock:
            times = list(self._rate_times)
            accepted = self._accepted
            rejected = self._rejected
        rate = 0.0
        if len(times) >= 2 and times[-1] > times[0]:
            rate = (len(times) - 1) / (times[-1] - times[0])
        return {
            "accepted": accepted,
            "rejected": rejected,
            "rate_hz": rate,
            "state": self.state,
            "reason": self.reason,
        }

    def _loop(self, port_name: str, baudrate: int) -> None:
        try:
            import serial

            port = serial.Serial(port_name, baudrate, timeout=0.20)
            port.reset_input_buffer()
            self._port = port
            self.state = "已连接"
            self.reason = "connected"
            while not self._stop.is_set():
                raw = port.read_until(b"\n", 257)
                if not raw:
                    continue
                if len(raw) > 256 or not raw.endswith(b"\n"):
                    with self._lock:
                        self._rejected += 1
                    self.reason = "line_too_long"
                    port.reset_input_buffer()
                    continue
                line = raw.decode("utf-8", errors="replace").strip()
                try:
                    frame = parse_data_line(line)
                except ProtocolError as exc:
                    with self._lock:
                        self._rejected += 1
                    self.reason = str(exc)
                    continue
                if frame is None:
                    self.last_log = line
                    continue
                if frame.range_status != 0 or not 30 <= frame.distance_mm <= 2000:
                    with self._lock:
                        self._rejected += 1
                    self.reason = f"sensor_status_{frame.range_status}"
                    continue
                now = time.monotonic()
                with self._lock:
                    self._samples.append(frame.distance_mm / 1000.0)
                    self._last_valid_monotonic = now
                    self._accepted += 1
                    self._rate_times.append(now)
                self.reason = "valid"
        except Exception as exc:
            self.state = "故障"
            self.reason = "serial_error"
            self.last_log = f"ToF 串口错误：{exc}"
        finally:
            if self._port is not None:
                try:
                    self._port.close()
                except Exception:
                    pass
            self._port = None


class LabStore:
    def __init__(self) -> None:
        self.root = Path.home() / "AppData" / "Roaming" / "ProjectLINK" / "visual_grasp_lab"
        self.config_path = self.root / "config.json"
        self.positions_path = self.root / "positions.json"
        self.debug_log_directory = self.root / "logs"

    def load_config(self, defaults: dict) -> dict:
        if not self.config_path.exists():
            return dict(defaults)
        try:
            saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return dict(defaults)
        result = dict(defaults)
        if isinstance(saved, dict):
            result.update(saved)
        return result

    def save_config(self, config: dict) -> None:
        self._write_json(self.config_path, config)

    def load_positions(self) -> dict:
        if not self.positions_path.exists():
            return {}
        try:
            data = json.loads(self.positions_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def save_positions(self, positions: dict) -> None:
        self._write_json(self.positions_path, positions)

    def _write_json(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
