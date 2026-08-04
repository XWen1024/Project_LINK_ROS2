"""Bounded incremental decoder for BU04 ``JSLLLL<payload>`` frames."""

from __future__ import annotations


class JsFrameDecoder:
    """Decode BU04 JSON frames without relying on line endings."""

    def __init__(self, max_payload_bytes: int = 4096) -> None:
        if max_payload_bytes < 2:
            raise ValueError("max_payload_bytes must be at least 2")
        self._max_payload_bytes = max_payload_bytes
        self._buffer = bytearray()

    @property
    def buffered_bytes(self) -> int:
        return len(self._buffer)

    def feed(self, data: bytes) -> list[bytes]:
        if data:
            self._buffer.extend(data)
        frames: list[bytes] = []
        while True:
            header_index = self._buffer.find(b"JS")
            if header_index < 0:
                self._keep_possible_prefix()
                break
            if header_index:
                del self._buffer[:header_index]
            if len(self._buffer) < 6:
                break
            length_bytes = bytes(self._buffer[2:6])
            try:
                payload_length = int(length_bytes.decode("ascii"), 16)
            except (UnicodeDecodeError, ValueError):
                del self._buffer[0]
                continue
            if payload_length < 2 or payload_length > self._max_payload_bytes:
                del self._buffer[0]
                continue
            frame_length = 6 + payload_length
            if len(self._buffer) < frame_length:
                self._bound_buffer()
                break
            frames.append(bytes(self._buffer[6:frame_length]))
            del self._buffer[:frame_length]
        self._bound_buffer()
        return frames

    def _keep_possible_prefix(self) -> None:
        if self._buffer.endswith(b"J"):
            self._buffer[:] = b"J"
        else:
            self._buffer.clear()

    def _bound_buffer(self) -> None:
        maximum = self._max_payload_bytes + 6
        if len(self._buffer) <= maximum:
            return
        tail = bytes(self._buffer[-maximum:])
        index = tail.find(b"JS")
        self._buffer[:] = tail[index:] if index >= 0 else tail[-1:]
