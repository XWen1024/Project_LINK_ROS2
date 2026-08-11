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
