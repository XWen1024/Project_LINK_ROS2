#!/usr/bin/env python3
"""List serial and audio devices for the standalone voice car demo."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys


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
    print("\n=== PulseAudio sinks ===")
    try:
        completed = subprocess.run(
            ["pactl", "list", "short", "sinks"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        print("pactl not installed")
    else:
        print(completed.stdout.strip() or completed.stderr.strip() or "no sinks found")


def scan_env(require_asr: bool = False) -> bool:
    print("\n=== Voice cloud env ===")
    provider = os.environ.get("PROJECT_LINK_ASR_PROVIDER", "volcano").strip().lower()
    print(f"PROJECT_LINK_ASR_PROVIDER: {provider}")
    print(
        "VOLCANO_ASR_ENDPOINT: "
        + os.environ.get(
            "VOLCANO_ASR_ENDPOINT",
            "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async",
        )
    )
    print(
        "VOLCANO_ASR_RESOURCE_ID: "
        + os.environ.get("VOLCANO_ASR_RESOURCE_ID", "volc.seedasr.sauc.duration")
    )
    for name in (
        "VOLCANO_ASR_API_KEY",
        "VOLCANO_ASR_APP_ID",
        "VOLCANO_ASR_ACCESS_TOKEN",
        "DEEPSEEK_API_KEY",
        "VOLCANO_APP_ID",
        "VOLCANO_ACCESS_TOKEN",
        "VOLCANO_RESOURCE_ID",
        "VOLCANO_SPEAKER",
        "PROJECT_LINK_WAKEUP_SERIAL",
        "PROJECT_LINK_AUDIO_INPUT_NAME",
        "PROJECT_LINK_AUDIO_OUTPUT_DEVICE",
    ):
        print(f"{name}: {'set' if os.environ.get(name) else 'missing'}")
    volcano_ready = bool(os.environ.get("VOLCANO_ASR_API_KEY")) or bool(
        os.environ.get("VOLCANO_ASR_APP_ID")
        and os.environ.get("VOLCANO_ASR_ACCESS_TOKEN")
    )
    if require_asr and provider == "volcano" and not volcano_ready:
        print(
            "ERROR: Volcano ASR is selected but its API key or legacy ASR app/token pair is missing.",
            file=sys.stderr,
        )
        print(
            "Set VOLCANO_ASR_API_KEY, or explicitly select PROJECT_LINK_ASR_PROVIDER=faster_whisper.",
            file=sys.stderr,
        )
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-only", action="store_true")
    parser.add_argument("--require-asr", action="store_true")
    arguments = parser.parse_args()
    if not arguments.env_only:
        scan_serial()
        scan_audio()
    return 0 if scan_env(arguments.require_asr) else 2


if __name__ == "__main__":
    raise SystemExit(main())
