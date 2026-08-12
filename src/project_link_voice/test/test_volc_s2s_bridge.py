import socket
import time

from project_link_voice.volc_s2s_bridge import BridgeFrame, HEADER, encode_frame, read_frame


def test_bridge_frame_round_trip() -> None:
    left, right = socket.socketpair()
    try:
        packet = encode_frame(131, b"pcm", flags=7, monotonic_ns=123456789)
        left.sendall(packet)
        frame = read_frame(right)
    finally:
        left.close()
        right.close()
    assert frame == BridgeFrame(131, 7, 123456789, b"pcm")


def test_bridge_frame_uses_monotonic_timestamp() -> None:
    before = time.monotonic_ns()
    packet = encode_frame(6)
    after = time.monotonic_ns()
    _message_type, _version, _flags, _length, timestamp = HEADER.unpack(packet[: HEADER.size])
    assert before <= timestamp <= after
