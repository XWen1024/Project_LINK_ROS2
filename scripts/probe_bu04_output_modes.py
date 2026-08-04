#!/usr/bin/env python3
"""Temporarily compare BU04 Hex and JSON UART output without saving."""

from __future__ import annotations

import argparse
import time

import serial


def exchange(port: serial.Serial, command: str, timeout_sec: float = 2.0) -> bytes:
    port.reset_input_buffer()
    port.write(command.encode("ascii") + b"\r\n")
    port.flush()
    response = bytearray()
    deadline = time.monotonic() + timeout_sec
    last_data = None
    while time.monotonic() < deadline:
        chunk = port.read(512)
        if chunk:
            response.extend(chunk)
            last_data = time.monotonic()
        elif last_data is not None and time.monotonic() - last_data >= 0.35:
            break
    return bytes(response)


def capture(port: serial.Serial, duration_sec: float) -> bytes:
    port.reset_input_buffer()
    data = bytearray()
    deadline = time.monotonic() + duration_sec
    while time.monotonic() < deadline:
        data.extend(port.read(1024))
    return bytes(data)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="/dev/uwb-bu04-at")
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument("--duration", type=float, default=15.0)
    args = parser.parse_args()

    with serial.Serial(args.device, args.baudrate, timeout=0.1) as port:
        try:
            print(f"SET_HEX={exchange(port, 'AT+USER_CMD=1')!r}")
            raw = capture(port, args.duration)
            print(f"HEX_BYTES={len(raw)}")
            print(f"HEX_PREVIEW={raw[:128]!r}")
        finally:
            print(f"RESTORE_JSON={exchange(port, 'AT+USER_CMD=0')!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
