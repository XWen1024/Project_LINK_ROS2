#!/usr/bin/env python3
"""List serial and audio devices for the standalone voice car demo."""

from __future__ import annotations

import os


def scan_serial() -> None:
    print("=== Serial ports ===")
    try:
        from serial.tools import list_ports
    except ImportError:
        print("pyserial not installed")
        return
    ports = list(list_ports.comports())
    if not ports:
        print("no serial ports found")
        return
    for port in ports:
        print(f"{port.device}\t{port.description}\t{port.hwid}")


def scan_audio() -> None:
    print("\n=== PyAudio devices ===")
    try:
        import pyaudio
    except ImportError:
        print("pyaudio not installed")
        return
    audio = pyaudio.PyAudio()
    try:
        for index in range(audio.get_device_count()):
            info = audio.get_device_info_by_index(index)
            inputs = int(info.get("maxInputChannels", 0))
            outputs = int(info.get("maxOutputChannels", 0))
            if inputs or outputs:
                role = []
                if inputs:
                    role.append(f"in={inputs}")
                if outputs:
                    role.append(f"out={outputs}")
                print(f"{index}\t{'/'.join(role)}\t{info.get('name')}")
    finally:
        audio.terminate()


def scan_env() -> None:
    print("\n=== Cloud/TTS env ===")
    for name in ("SILICONFLOW_API_KEY", "VOLCANO_APP_ID", "VOLCANO_ACCESS_TOKEN", "VOLCANO_RESOURCE_ID", "VOLCANO_SPEAKER"):
        print(f"{name}: {'set' if os.environ.get(name) else 'missing'}")


def main() -> int:
    scan_serial()
    scan_audio()
    scan_env()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
