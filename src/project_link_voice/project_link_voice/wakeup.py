from __future__ import annotations

import glob
from collections.abc import Callable


class SerialWakeDetector:
    def __init__(self, match_text: str, max_buffer_bytes: int = 16384) -> None:
        self._needle = match_text.encode("utf-8")
        self._max_buffer_bytes = max(max_buffer_bytes, len(self._needle) + 1)
        self._buffer = bytearray()

    def feed(self, data: bytes) -> str | None:
        if not data:
            return None
        if not self._needle:
            return data.decode("utf-8", errors="backslashreplace")
        self._buffer.extend(data)
        match_index = self._buffer.find(self._needle)
        if match_index >= 0:
            end = match_index + len(self._needle)
            start = max(0, end - 2048)
            matched = bytes(self._buffer[start:end])
            self._buffer.clear()
            return matched.decode("utf-8", errors="backslashreplace")
        if len(self._buffer) > self._max_buffer_bytes:
            keep = max(len(self._needle) - 1, self._max_buffer_bytes // 2)
            del self._buffer[:-keep]
        return None


def resolve_wakeup_serial_port(
    configured: str,
    log_warning: Callable[[str], None] | None = None,
) -> str:
    value = configured.strip()
    if value and value.lower() != "auto":
        return value
    by_id_matches = sorted(glob.glob("/dev/serial/by-id/*WCH.CN_USB_Single_Serial_0004*"))
    if by_id_matches:
        selected = by_id_matches[0]
        if log_warning:
            log_warning(f"Auto-selected iFlytek wake serial: {selected}")
        return selected
    try:
        from serial.tools import list_ports

        ports = list(list_ports.comports())
    except Exception:
        ports = []
    exact = [port for port in ports if (port.serial_number or "") == "0004"]
    preferred = exact or [
        port
        for port in ports
        if "USB" in (port.description or "").upper() or "SERIAL" in (port.description or "").upper()
    ]
    if preferred:
        selected = preferred[0].device
        if log_warning:
            log_warning(f"Auto-selected wake serial from enumerated ports: {selected}")
        return selected
    return "/dev/ttyUSB0"
