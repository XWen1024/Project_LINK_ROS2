#!/usr/bin/env python3
"""Print raw serial wakeup data from a serial port."""

from __future__ import annotations

import argparse
import sys
import time


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM9", help="Serial port, default: COM9")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate, default: 115200")
    parser.add_argument(
        "--seconds",
        type=float,
        default=0.0,
        help="Optional test duration. 0 means run until Ctrl+C.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    try:
        import serial
    except ImportError:
        print("pyserial is not installed. Install it with: python -m pip install pyserial", flush=True)
        return 2

    deadline = time.monotonic() + args.seconds if args.seconds > 0 else None
    print(f"Opening {args.port} at {args.baud} baud. Press Ctrl+C to stop.", flush=True)
    with serial.Serial(args.port, args.baud, timeout=0.1) as ser:
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                print("Timeout reached; exiting.", flush=True)
                return 0
            data = ser.readline()
            if not data:
                continue
            decoded = data.decode("utf-8", errors="backslashreplace").rstrip("\r\n")
            print(f"{time.strftime('%H:%M:%S')} raw={data!r} text={decoded}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
