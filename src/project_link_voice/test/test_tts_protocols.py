import struct

from project_link_voice.tts_protocols import (
    EventType,
    Message,
    MsgType,
    MsgTypeFlagBits,
    SerializationBits,
)


def test_audio_response_accepts_no_serialization_and_tts_event():
    session_id = b"session-1"
    pcm = b"\x01\x02\x03\x04"
    frame = (
        bytes([0x11, 0xB4, 0x00, 0x00])
        + struct.pack(">i", EventType.TTSResponse)
        + struct.pack(">I", len(session_id))
        + session_id
        + struct.pack(">I", len(pcm))
        + pcm
    )

    message = Message.from_bytes(frame)

    assert message.type == MsgType.AudioOnlyServer
    assert message.flag == MsgTypeFlagBits.WithEvent
    assert message.serialization == SerializationBits.None_
    assert message.event == EventType.TTSResponse
    assert message.session_id == "session-1"
    assert message.payload == pcm


def test_client_event_frame_keeps_json_serialization_header():
    message = Message(
        type=MsgType.FullClientRequest,
        flag=MsgTypeFlagBits.WithEvent,
        event=EventType.StartConnection,
        payload=b"{}",
    )

    frame = message.marshal()

    assert frame[:4] == bytes([0x11, 0x14, 0x10, 0x00])


def test_cancel_session_frame_preserves_session_id():
    message = Message(
        type=MsgType.FullClientRequest,
        flag=MsgTypeFlagBits.WithEvent,
        event=EventType.CancelSession,
        session_id="session-1",
        payload=b"{}",
    )

    decoded = Message.from_bytes(message.marshal())

    assert decoded.event == EventType.CancelSession
    assert decoded.session_id == "session-1"


def test_tts_sentence_events_and_future_events_do_not_break_parsing():
    sentence_start = bytes([0x11, 0x94, 0x10, 0x00]) + struct.pack(">i", 350)
    sentence_start += struct.pack(">I", 0) + struct.pack(">I", 2) + b"{}"
    future_event = bytes([0x11, 0x94, 0x10, 0x00]) + struct.pack(">i", 399)
    future_event += struct.pack(">I", 0) + struct.pack(">I", 2) + b"{}"

    start_message = Message.from_bytes(sentence_start)
    future_message = Message.from_bytes(future_event)

    assert start_message.event == EventType.TTSSentenceStart
    assert future_message.event.value == 399
    assert future_message.event.name == "Unknown_399"
