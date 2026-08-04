#!/usr/bin/env python3
"""Operator-only BU04 PDoA mode switch; persistence is explicit and separate."""

from __future__ import annotations

import argparse
import time

import serial


def exchange(port: serial.Serial, command: bytes, timeout_sec: float = 2.0) -> bytes:
    port.write(command + b"\r\n")
    port.flush()
    response = bytearray()
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        chunk = port.read(256)
        if chunk:
            response.extend(chunk)
    return bytes(response)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="/dev/uwb-bu04-at")
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument("--confirm", required=True)
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args()
    if args.confirm != "SET-PDOA":
        raise SystemExit("refusing: --confirm SET-PDOA is required")

    with serial.Serial(args.device, args.baudrate, timeout=0.1) as port:
        response = exchange(port, b"AT+SETUWBMODE=1")
        print(f"SETUWBMODE response={response!r}")
        if args.save:
            save_response = exchange(port, b"AT+SAVE")
            print(f"SAVE response={save_response!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
