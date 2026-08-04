#!/usr/bin/env python3
"""Read-only BU04 serial probe that never prints the private tag address."""

from __future__ import annotations

import argparse
import json
import re
import time

import serial
from serial import SerialException


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="/dev/uwb-bu04")
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument("--duration", type=float, default=8.0)
    parser.add_argument("--authenticate", action="store_true")
    args = parser.parse_args()

    buffer = bytearray()
    byte_count = 0
    frame_count = 0
    valid_count = 0
    disconnect_count = 0
    authentication_count = 0
    authentication_response = ""
    samples: list[dict[str, object]] = []
    preview = bytearray()
    deadline = time.monotonic() + args.duration

    while time.monotonic() < deadline:
        try:
            port = serial.Serial(args.device, args.baudrate, timeout=0.1)
        except (OSError, SerialException):
            time.sleep(0.1)
            continue
        try:
            if args.authenticate:
                port.reset_input_buffer()
                port.write(b"AT+DECA$\r\n")
                port.flush()
                auth_raw = bytearray()
                auth_deadline = time.monotonic() + 2.0
                while time.monotonic() < auth_deadline:
                    auth_chunk = port.read(256)
                    if auth_chunk:
                        auth_raw.extend(auth_chunk)
                        if b"OK" in auth_raw:
                            break
                authentication_count += 1
                authentication_response = auth_raw.decode("utf-8", errors="backslashreplace")
            while time.monotonic() < deadline:
                try:
                    chunk = port.read(1024)
                except (OSError, SerialException):
                    disconnect_count += 1
                    break
                if not chunk:
                    continue
                byte_count += len(chunk)
                if len(preview) < 256:
                    preview.extend(chunk[: 256 - len(preview)])
                buffer.extend(chunk)
                while True:
                    start = buffer.find(b"JS")
                    if start < 0:
                        buffer[:] = buffer[-1:] if buffer.endswith(b"J") else b""
                        break
                    if start:
                        del buffer[:start]
                    if len(buffer) < 6:
                        break
                    try:
                        payload_length = int(bytes(buffer[2:6]).decode("ascii"), 16)
                    except (UnicodeDecodeError, ValueError):
                        del buffer[0]
                        continue
                    if payload_length < 2 or payload_length > 4096:
                        del buffer[0]
                        continue
                    total = 6 + payload_length
                    if len(buffer) < total:
                        break
                    payload = bytes(buffer[6:total])
                    del buffer[:total]
                    frame_count += 1
                    try:
                        root = json.loads(payload.decode("utf-8"))
                        twr = root.get("TWR", {}) if isinstance(root, dict) else {}
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    if not isinstance(twr, dict):
                        continue
                    valid_count += 1
                    if len(samples) < 3:
                        samples.append(
                            {
                                "T": twr.get("T"),
                                "D": twr.get("D"),
                                "Xcm": twr.get("Xcm"),
                                "Ycm": twr.get("Ycm"),
                                "fields": sorted(key for key in twr if key != "a16"),
                                "a16": "<redacted>" if "a16" in twr else "<missing>",
                            }
                        )
        finally:
            port.close()
        if time.monotonic() < deadline:
            time.sleep(0.1)

    preview_text = preview.decode("utf-8", errors="backslashreplace")
    preview_text = re.sub(r'("a16"\s*:\s*")[^"]+("?)', r'\1<redacted>\2', preview_text)
    print(
        json.dumps(
            {
                "device": args.device,
                "baudrate": args.baudrate,
                "duration_sec": args.duration,
                "bytes": byte_count,
                "framed_messages": frame_count,
                "valid_json_messages": valid_count,
                "disconnects": disconnect_count,
                "authentications": authentication_count,
                "authentication_response": authentication_response,
                "redacted_preview": preview_text,
                "samples": samples,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if valid_count else 1


if __name__ == "__main__":
    raise SystemExit(main())
