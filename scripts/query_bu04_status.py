#!/usr/bin/env python3
"""Query documented BU04 status without modifying or saving configuration."""

from __future__ import annotations

import argparse
import re
import time

import serial


COMMANDS = (
    "AT",
    "AT+GETVER",
    "AT+GETUWBMODE",
    "AT+GETCFG",
    "AT+PDOAGETCFG",
    "AT+GETDLIST",
    "AT+GETKLIST",
    "AT+DISTANCE",
)


def redact(text: str) -> str:
    text = re.sub(r'("a(?:16|64)"\s*:\s*")[^"]+("?)', r'\1<redacted>\2', text, flags=re.I)
    text = re.sub(r"\b[0-9A-Fa-f]{16}\b", "<redacted-a64>", text)
    return text


def exchange(port: serial.Serial, command: str) -> bytes:
    port.reset_input_buffer()
    port.write(command.encode("ascii") + b"\r\n")
    port.flush()
    response = bytearray()
    deadline = time.monotonic() + 2.5
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
    args = parser.parse_args()
    with serial.Serial(args.device, args.baudrate, timeout=0.1) as port:
        for command in COMMANDS:
            raw = exchange(port, command)
            decoded = raw.decode("utf-8", errors="backslashreplace")
            print(f"===== {command} =====")
            print(redact(decoded).strip() or "<no response>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
