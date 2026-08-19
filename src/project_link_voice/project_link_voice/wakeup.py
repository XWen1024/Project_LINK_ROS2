from __future__ import annotations

import glob
import os
from collections.abc import Callable


IFLYTEK_WAKE_BY_ID = "/dev/serial/by-id/usb-WCH.CN_USB_Single_Serial_0004-if00"
DEFAULT_WAKEUP_ALIAS = "/dev/project_link_wakeup"


class SerialWakeDetector:
    def __init__(self, match_text: str, max_buffer_bytes: int = 16384) -> None:
        self._needle = match_text.encode("utf-8")
        self._needle_folded = self._needle.lower()
        self._max_buffer_bytes = max(max_buffer_bytes, len(self._needle) + 1)
        self._buffer = bytearray()

    def feed(self, data: bytes) -> str | None:
        if not data:
            return None
        if not self._needle:
            return data.decode("utf-8", errors="backslashreplace")
        self._buffer.extend(data)
        # AIUI firmware revisions have emitted both lower- and upper-case ASCII
        # event keys. Matching case-insensitively keeps the binary rolling-buffer
        # behavior while avoiding a silent wake failure after firmware changes.
        match_index = bytes(self._buffer).lower().find(self._needle_folded)
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
        if os.path.exists(value):
            return value
        if value == DEFAULT_WAKEUP_ALIAS and os.path.exists(IFLYTEK_WAKE_BY_ID):
            if log_warning:
                log_warning(
                    f"Wake alias {DEFAULT_WAKEUP_ALIAS} is missing; using stable by-id path: "
                    f"{IFLYTEK_WAKE_BY_ID}"
                )
            return IFLYTEK_WAKE_BY_ID
        return value
    if os.path.exists(DEFAULT_WAKEUP_ALIAS):
        if log_warning:
            log_warning(f"Auto-selected Project LINK wake alias: {DEFAULT_WAKEUP_ALIAS}")
        return DEFAULT_WAKEUP_ALIAS
    if os.path.exists(IFLYTEK_WAKE_BY_ID):
        if log_warning:
            log_warning(f"Auto-selected iFlytek wake serial: {IFLYTEK_WAKE_BY_ID}")
        return IFLYTEK_WAKE_BY_ID
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
