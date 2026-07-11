"""Volcano bidirectional TTS protocol helpers."""

from __future__ import annotations

import io
import logging
import struct
from dataclasses import dataclass
from enum import IntEnum

logger = logging.getLogger(__name__)


class MsgType(IntEnum):
    Invalid = 0
    FullClientRequest = 0b1
    AudioOnlyClient = 0b10
    FullServerResponse = 0b1001
    AudioOnlyServer = 0b1011
    FrontEndResultServer = 0b1100
    Error = 0b1111


class MsgTypeFlagBits(IntEnum):
    NoSeq = 0
    PositiveSeq = 0b1
    NegativeSeq = 0b11
    WithEvent = 0b100


class VersionBits(IntEnum):
    Version1 = 1


class HeaderSizeBits(IntEnum):
    HeaderSize4 = 1


class SerializationBits(IntEnum):
    JSON = 0b1


class CompressionBits(IntEnum):
    None_ = 0


class EventType(IntEnum):
    None_ = 0
    StartConnection = 1
    FinishConnection = 2
    ConnectionStarted = 50
    ConnectionFailed = 51
    ConnectionFinished = 52
    StartSession = 100
    CancelSession = 101
    FinishSession = 102
    SessionStarted = 150
    SessionCanceled = 151
    SessionFinished = 152
    SessionFailed = 153
    TaskRequest = 200


@dataclass
class Message:
    version: VersionBits = VersionBits.Version1
    header_size: HeaderSizeBits = HeaderSizeBits.HeaderSize4
    type: MsgType = MsgType.Invalid
    flag: MsgTypeFlagBits = MsgTypeFlagBits.NoSeq
    serialization: SerializationBits = SerializationBits.JSON
    compression: CompressionBits = CompressionBits.None_
    event: EventType = EventType.None_
    session_id: str = ""
    connect_id: str = ""
    sequence: int = 0
    error_code: int = 0
    payload: bytes = b""

    @classmethod
    def from_bytes(cls, data: bytes) -> "Message":
        if len(data) < 3:
            raise ValueError(f"Data too short: expected at least 3 bytes, got {len(data)}")
        msg_type = MsgType(data[1] >> 4)
        flag = MsgTypeFlagBits(data[1] & 0b00001111)
        message = cls(type=msg_type, flag=flag)
        message.unmarshal(data)
        return message

    def marshal(self) -> bytes:
        buffer = io.BytesIO()
        header = [
            (self.version << 4) | self.header_size,
            (self.type << 4) | self.flag,
            (self.serialization << 4) | self.compression,
            0,
        ]
        buffer.write(bytes(header))
        if self.flag == MsgTypeFlagBits.WithEvent:
            buffer.write(struct.pack(">i", self.event))
            if self.event not in (EventType.StartConnection, EventType.FinishConnection):
                self._write_string(buffer, self.session_id)
        if self.flag in (MsgTypeFlagBits.PositiveSeq, MsgTypeFlagBits.NegativeSeq):
            buffer.write(struct.pack(">i", self.sequence))
        if self.type == MsgType.Error:
            buffer.write(struct.pack(">I", self.error_code))
        buffer.write(struct.pack(">I", len(self.payload)))
        buffer.write(self.payload)
        return buffer.getvalue()

    def unmarshal(self, data: bytes) -> None:
        buffer = io.BytesIO(data)
        version_and_size = buffer.read(1)[0]
        self.version = VersionBits(version_and_size >> 4)
        self.header_size = HeaderSizeBits(version_and_size & 0b00001111)
        buffer.read(1)
        serialization_compression = buffer.read(1)[0]
        self.serialization = SerializationBits(serialization_compression >> 4)
        self.compression = CompressionBits(serialization_compression & 0b00001111)
        buffer.read((4 * self.header_size) - 3)
        if self.flag in (MsgTypeFlagBits.PositiveSeq, MsgTypeFlagBits.NegativeSeq):
            self.sequence = struct.unpack(">i", buffer.read(4))[0]
        if self.type == MsgType.Error:
            self.error_code = struct.unpack(">I", buffer.read(4))[0]
        if self.flag == MsgTypeFlagBits.WithEvent:
            event_bytes = buffer.read(4)
            if event_bytes:
                self.event = EventType(struct.unpack(">i", event_bytes)[0])
            if self.event not in (
                EventType.StartConnection,
                EventType.FinishConnection,
                EventType.ConnectionStarted,
                EventType.ConnectionFailed,
                EventType.ConnectionFinished,
            ):
                self.session_id = self._read_string(buffer)
            if self.event in (EventType.ConnectionStarted, EventType.ConnectionFailed, EventType.ConnectionFinished):
                self.connect_id = self._read_string(buffer)
        size_bytes = buffer.read(4)
        if size_bytes:
            size = struct.unpack(">I", size_bytes)[0]
            self.payload = buffer.read(size)

    @staticmethod
    def _write_string(buffer: io.BytesIO, value: str) -> None:
        data = value.encode("utf-8")
        buffer.write(struct.pack(">I", len(data)))
        buffer.write(data)

    @staticmethod
    def _read_string(buffer: io.BytesIO) -> str:
        size_bytes = buffer.read(4)
        if not size_bytes:
            return ""
        size = struct.unpack(">I", size_bytes)[0]
        return buffer.read(size).decode("utf-8") if size else ""


async def receive_message(websocket) -> Message:
    data = await websocket.recv()
    if not isinstance(data, bytes):
        raise ValueError(f"Unexpected text message: {data}")
    message = Message.from_bytes(data)
    logger.debug("Received TTS message type=%s event=%s bytes=%d", message.type, message.event, len(message.payload))
    return message


async def wait_for_event(websocket, msg_type: MsgType, event_type: EventType) -> Message:
    message = await receive_message(websocket)
    if message.type != msg_type or message.event != event_type:
        raise ValueError(f"Unexpected TTS message: type={message.type} event={message.event}")
    return message


async def start_connection(websocket) -> None:
    message = Message(type=MsgType.FullClientRequest, flag=MsgTypeFlagBits.WithEvent, event=EventType.StartConnection)
    message.payload = b"{}"
    await websocket.send(message.marshal())


async def start_session(websocket, payload: bytes, session_id: str) -> None:
    message = Message(type=MsgType.FullClientRequest, flag=MsgTypeFlagBits.WithEvent, event=EventType.StartSession)
    message.session_id = session_id
    message.payload = payload
    await websocket.send(message.marshal())


async def finish_session(websocket, session_id: str) -> None:
    message = Message(type=MsgType.FullClientRequest, flag=MsgTypeFlagBits.WithEvent, event=EventType.FinishSession)
    message.session_id = session_id
    message.payload = b"{}"
    await websocket.send(message.marshal())


async def task_request(websocket, payload: bytes, session_id: str) -> None:
    message = Message(type=MsgType.FullClientRequest, flag=MsgTypeFlagBits.WithEvent, event=EventType.TaskRequest)
    message.session_id = session_id
    message.payload = payload
    await websocket.send(message.marshal())
