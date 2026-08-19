"""Small, hardware-independent helpers for the visual-grasp camera stream."""

from __future__ import annotations


def native_mjpeg_command(device: str, width: int, height: int, fps: float) -> list[str]:
    """Build the bounded v4l2-ctl command used for zero-reencode MJPEG capture."""
    return [
        "v4l2-ctl",
        "-d",
        device,
        f"--set-fmt-video=width={width},height={height},pixelformat=MJPG",
        f"--set-parm={fps:g}",
        "--stream-mmap=3",
        "--stream-count=0",
        "--stream-to=-",
    ]


def pop_native_jpeg(buffer: bytearray) -> bytes | None:
    """Pop one complete JPEG and discard a truncated frame before a newer SOI."""
    while True:
        start = buffer.find(b"\xff\xd8")
        if start < 0:
            if len(buffer) > 1:
                del buffer[:-1]
            return None
        if start > 0:
            del buffer[:start]

        end = buffer.find(b"\xff\xd9", 2)
        next_start = buffer.find(b"\xff\xd8", 2)
        if next_start >= 0 and (end < 0 or next_start < end):
            del buffer[:next_start]
            continue
        if end < 0:
            if len(buffer) > 8 * 1024 * 1024:
                buffer.clear()
            return None

        jpeg = bytes(buffer[: end + 2])
        del buffer[: end + 2]
        if len(jpeg) >= 1024:
            return jpeg
