import json
import socket
import threading
import time

import pytest

from project_link_voice.volc_s2s_bridge import (
    BridgeFrame,
    HEADER,
    VolcS2SBridgeProcess,
    encode_frame,
    read_frame,
)


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


def test_command_result_can_be_waited_by_client_timestamp():
    bridge = VolcS2SBridgeProcess("/not-started", lambda _frame: None)
    result = []

    waiter = threading.Thread(target=lambda: result.append(bridge.wait_command_result(123, 1.0)))
    waiter.start()
    bridge._handle_control(
        json.dumps(
            {
                "event": "command_result",
                "command": "clear",
                "result": -7,
                "client_timestamp_ns": 123,
            }
        ).encode()
    )
    waiter.join(timeout=1.0)
    assert result == [-7]


def test_command_result_wait_rejects_bridge_reader_thread():
    bridge = VolcS2SBridgeProcess("/not-started", lambda _frame: None)
    bridge._reader_thread = threading.current_thread()
    with pytest.raises(RuntimeError, match="bridge reader thread"):
        bridge.wait_command_result(123, 0.01)
