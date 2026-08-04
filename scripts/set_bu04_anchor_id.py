#!/usr/bin/env python3
"""Set the documented BU04 base-station ID while preserving other settings."""

from __future__ import annotations

import argparse
import time

import serial


def exchange(port: serial.Serial, command: str, timeout_sec: float = 2.5) -> bytes:
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="/dev/uwb-bu04-at")
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument("--anchor-id", type=int, default=1)
    parser.add_argument("--network-decimal", type=int, default=4369)
    parser.add_argument("--confirm", required=True)
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args()
    if args.confirm != "SET-ANCHOR-ID":
        raise SystemExit("refusing: --confirm SET-ANCHOR-ID is required")
    if not 1 <= args.anchor_id <= 4:
        raise SystemExit("anchor ID must be in the documented range 1..4")

    if not 0 <= args.network_decimal <= 65534:
        raise SystemExit("network decimal value must be in 0..65534")
    command = f"AT+PDOASETCFG=1,1,{args.network_decimal},{args.anchor_id},100,1,0"
    with serial.Serial(args.device, args.baudrate, timeout=0.1) as port:
        response = exchange(port, command)
        print(f"PDOASETCFG response={response!r}")
        verify = exchange(port, "AT+PDOAGETCFG")
        print(f"PDOAGETCFG response={verify!r}")
        if args.save:
            save_response = exchange(port, "AT+SAVE")
            print(f"SAVE response={save_response!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
