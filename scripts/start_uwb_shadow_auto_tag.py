#!/usr/bin/env python3
"""Start UWB shadow with a single auto-discovered private BU04 tag.

The discovered address is never printed or written to disk. This entrypoint is
hard-locked to shadow mode; live motion still requires the guarded launcher and
an explicitly supplied private tag address.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import time


EXPECTED_USB_VENDOR = "0483"
EXPECTED_USB_MODEL = "5740"
DEFAULT_SESSION = "project_link_uwb_navigation"


def extract_addresses(buffer: bytearray, chunk: bytes) -> set[str]:
    """Consume BU04 JS frames and return private addresses without logging them."""
    addresses: set[str] = set()
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
        try:
            root = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        twr = root.get("TWR", {}) if isinstance(root, dict) else {}
        address = twr.get("a16") if isinstance(twr, dict) else None
        if isinstance(address, str) and address.strip():
            addresses.add(address.strip())
    return addresses


def verify_device_identity(device: str) -> None:
    if not Path(device).exists():
        raise RuntimeError(f"BU04 device does not exist: {device}")
    result = subprocess.run(
        ["udevadm", "info", "-q", "property", "-n", device],
        check=True,
        capture_output=True,
        text=True,
    )
    properties = dict(
        line.split("=", maxsplit=1)
        for line in result.stdout.splitlines()
        if "=" in line
    )
    if (
        properties.get("ID_VENDOR_ID", "").lower() != EXPECTED_USB_VENDOR
        or properties.get("ID_MODEL_ID", "").lower() != EXPECTED_USB_MODEL
    ):
        raise RuntimeError("Selected device is not the expected BU04 STM32 USB interface")


def discover_single_tag(device: str, duration_sec: float) -> str:
    import serial

    addresses: set[str] = set()
    buffer = bytearray()
    deadline = time.monotonic() + duration_sec
    with serial.Serial(device, 115200, timeout=0.1) as port:
        while time.monotonic() < deadline:
            addresses.update(extract_addresses(buffer, port.read(1024)))
            if len(addresses) > 1:
                raise RuntimeError("Multiple private tags were observed; refusing auto-selection")
    if len(addresses) != 1:
        raise RuntimeError("No private tag was found in the BU04 stream")
    return next(iter(addresses))


def stop_existing_session(workspace: Path, session: str) -> None:
    if subprocess.run(
        ["tmux", "has-session", "-t", session],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode != 0:
        return
    try:
        subprocess.run(
            [str(workspace / "navigation_two_uwb.sh"), "stop"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5.0,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pass
    subprocess.run(["tmux", "kill-session", "-t", session], check=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Auto-discover one BU04 tag and start fail-safe UWB shadow only."
    )
    parser.add_argument("--device", default="/dev/uwb-bu04")
    parser.add_argument(
        "--workspace",
        default=os.environ.get("PROJECT_LINK_WORKSPACE", "/home/wte/wheeltec_robot"),
    )
    parser.add_argument("--params", default="")
    parser.add_argument("--restart", action="store_true")
    parser.add_argument("--attach", action="store_true")
    parser.add_argument("--discovery-duration", type=float, default=3.0)
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    launcher = workspace / "navigation_two_start_uwb.sh"
    if not launcher.is_file():
        raise RuntimeError(f"UWB launcher is missing: {launcher}")
    if args.discovery_duration <= 0.0 or args.discovery_duration > 15.0:
        raise RuntimeError("Discovery duration must be in (0, 15] seconds")

    verify_device_identity(args.device)
    session = os.environ.get("PROJECT_LINK_UWB_TMUX_SESSION", DEFAULT_SESSION)
    if args.restart:
        stop_existing_session(workspace, session)
    elif subprocess.run(
        ["tmux", "has-session", "-t", session],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0:
        raise RuntimeError(f"UWB session already exists: {session} (use --restart)")

    private_address = discover_single_tag(args.device, args.discovery_duration)
    environment = os.environ.copy()
    environment["PROJECT_LINK_UWB_TAG_ADDRESS"] = private_address
    environment["PROJECT_LINK_UWB_DEVICE"] = args.device
    environment["PROJECT_LINK_WORKSPACE"] = str(workspace)

    command = [
        "/usr/bin/bash",
        str(launcher),
        "--shadow",
        "--device",
        args.device,
    ]
    if args.params:
        command.extend(["--params", str(Path(args.params).expanduser())])
    if args.restart:
        command.append("--restart")
    if args.attach:
        command.append("--attach")

    os.chdir(workspace)
    os.execve(command[0], command, environment)


if __name__ == "__main__":
    raise SystemExit(main())
