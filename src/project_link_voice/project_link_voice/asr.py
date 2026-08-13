"""Switchable local Whisper and Volcano streaming ASR providers."""

from __future__ import annotations

import asyncio
import gzip
import inspect
import json
import os
import queue
import struct
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Protocol


DEFAULT_VOLCANO_ASR_ENDPOINT = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"
DEFAULT_VOLCANO_ASR_RESOURCE_ID = "volc.seedasr.sauc.duration"


class AsrTurn(Protocol):
    def feed_audio(self, pcm: bytes) -> None: ...

    def finish(self, pcm: bytes) -> str: ...

    def abort(self) -> None: ...


class AsrProvider(Protocol):
    name: str

    def warm_up(self) -> None: ...

    def begin_turn(
        self,
        timing_callback: Callable[[str, float, dict], None] | None = None,
        partial_callback: Callable[[str], None] | None = None,
    ) -> AsrTurn: ...


class WhisperTranscriber:
    name = "faster_whisper"

    def __init__(self, model_path: str, device: str, compute_type: str) -> None:
        self._model_path = model_path
        self._device = device
        self._compute_type = compute_type
        self._model = None

    def _model_instance(self):
        from faster_whisper import WhisperModel

        if self._model is None:
            try:
                self._model = WhisperModel(self._model_path, device=self._device, compute_type=self._compute_type)
            except Exception:
                self._model = WhisperModel(self._model_path, device="cpu", compute_type="int8")
        return self._model

    def warm_up(self) -> None:
        self._model_instance()

    def transcribe_pcm(self, pcm: bytes) -> str:
        import numpy as np

        model = self._model_instance()
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _ = model.transcribe(audio, language="zh")
        return "".join(segment.text for segment in segments).strip()

    def begin_turn(
        self,
        timing_callback: Callable[[str, float, dict], None] | None = None,
        partial_callback: Callable[[str], None] | None = None,
    ) -> "WhisperAsrTurn":
        del partial_callback
        return WhisperAsrTurn(self, timing_callback)


class WhisperAsrTurn:
    def __init__(
        self,
        transcriber: WhisperTranscriber,
        timing_callback: Callable[[str, float, dict], None] | None,
    ) -> None:
        self._transcriber = transcriber
        self._timing_callback = timing_callback

    def feed_audio(self, pcm: bytes) -> None:
        del pcm

    def finish(self, pcm: bytes) -> str:
        started_at = time.perf_counter()
        try:
            return self._transcriber.transcribe_pcm(pcm)
        finally:
            _notify_timing(
                self._timing_callback,
                "asr_final",
                started_at,
                {"provider": self._transcriber.name},
            )

    def abort(self) -> None:
        return


@dataclass(frozen=True)
class VolcanoAsrSettings:
    endpoint: str = DEFAULT_VOLCANO_ASR_ENDPOINT
    resource_id: str = DEFAULT_VOLCANO_ASR_RESOURCE_ID
    sample_rate: int = 16000
    packet_ms: int = 100
    final_timeout_sec: float = 2.0
    connect_timeout_sec: float = 5.0
    max_buffer_sec: float = 4.0
    enable_nonstream: bool = False
    enable_punc: bool = True
    enable_itn: bool = True
    enable_ddc: bool = True

    @property
    def packet_bytes(self) -> int:
        return int(self.sample_rate * self.packet_ms / 1000) * 2


@dataclass(frozen=True)
class VolcanoAsrResponse:
    code: int = 0
    event: int = 0
    is_last_package: bool = False
    sequence: int = 0
    payload: dict[str, Any] | None = None


def build_full_request(sequence: int, payload: dict[str, Any]) -> bytes:
    body = gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    header = bytes([0x11, 0x11, 0x11, 0x00])
    return header + struct.pack(">iI", sequence, len(body)) + body


def build_audio_request(sequence: int, pcm: bytes, is_last: bool = False) -> bytes:
    body = gzip.compress(pcm)
    flag = 0b0011 if is_last else 0b0001
    wire_sequence = -abs(sequence) if is_last else sequence
    header = bytes([0x11, (0b0010 << 4) | flag, 0x01, 0x00])
    return header + struct.pack(">iI", wire_sequence, len(body)) + body


def parse_response(data: bytes) -> VolcanoAsrResponse:
    if len(data) < 4:
        raise ValueError("Volcano ASR response is shorter than its header")
    header_size = (data[0] & 0x0F) * 4
    message_type = data[1] >> 4
    flags = data[1] & 0x0F
    serialization = data[2] >> 4
    compression = data[2] & 0x0F
    offset = header_size
    sequence = 0
    event = 0
    is_last = bool(flags & 0x02)
    if flags & 0x01:
        sequence = struct.unpack(">i", data[offset : offset + 4])[0]
        offset += 4
    if flags & 0x04:
        event = struct.unpack(">i", data[offset : offset + 4])[0]
        offset += 4
    code = 0
    if message_type == 0b1001:
        payload_size = struct.unpack(">I", data[offset : offset + 4])[0]
        offset += 4
    elif message_type == 0b1111:
        code = struct.unpack(">i", data[offset : offset + 4])[0]
        payload_size = struct.unpack(">I", data[offset + 4 : offset + 8])[0]
        offset += 8
    else:
        raise ValueError(f"Unsupported Volcano ASR response message type: {message_type}")
    payload_bytes = data[offset : offset + payload_size]
    if compression == 0b0001 and payload_bytes:
        payload_bytes = gzip.decompress(payload_bytes)
    payload = None
    if serialization == 0b0001 and payload_bytes:
        payload = json.loads(payload_bytes.decode("utf-8"))
    return VolcanoAsrResponse(code, event, is_last, sequence, payload)


def extract_transcript(payload: dict[str, Any] | None) -> tuple[str, bool]:
    if not isinstance(payload, dict):
        return "", False
    result = payload.get("result")
    if isinstance(result, dict):
        text = result.get("text")
        utterances = result.get("utterances")
        definite = False
        if isinstance(utterances, list):
            definite = any(isinstance(item, dict) and bool(item.get("definite")) for item in utterances)
        if isinstance(text, str) and text.strip():
            return text.strip(), definite
    if isinstance(result, list):
        texts = [item.get("text", "").strip() for item in result if isinstance(item, dict)]
        texts = [text for text in texts if text]
        if texts:
            definite = any(isinstance(item, dict) and bool(item.get("definite")) for item in result)
            return "".join(texts), definite
    candidates: list[tuple[str, bool]] = []

    def visit(value: Any, inherited_definite: bool = False) -> None:
        if isinstance(value, dict):
            definite = bool(value.get("definite", inherited_definite))
            text = value.get("text")
            if isinstance(text, str) and text.strip():
                candidates.append((text.strip(), definite))
            for key, item in value.items():
                if key != "text":
                    visit(item, definite)
        elif isinstance(value, list):
            for item in value:
                visit(item, inherited_definite)

    visit(payload)
    if not candidates:
        return "", False
    definite_candidates = [item for item in candidates if item[1]]
    return definite_candidates[-1] if definite_candidates else candidates[-1]


class VolcanoStreamingAsrProvider:
    name = "volcano"

    def __init__(self, settings: VolcanoAsrSettings) -> None:
        self.settings = settings
        self._api_key = os.environ.get("VOLCANO_ASR_API_KEY", "").strip()
        self._app_id = os.environ.get("VOLCANO_ASR_APP_ID", "").strip()
        self._access_token = os.environ.get("VOLCANO_ASR_ACCESS_TOKEN", "").strip()

    def warm_up(self) -> None:
        if not self._api_key and not (self._app_id and self._access_token):
            raise RuntimeError(
                "Volcano ASR credentials are missing; set VOLCANO_ASR_API_KEY or "
                "VOLCANO_ASR_APP_ID plus VOLCANO_ASR_ACCESS_TOKEN"
            )
        try:
            import websockets  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("websockets is required for Volcano streaming ASR") from exc

    def begin_turn(
        self,
        timing_callback: Callable[[str, float, dict], None] | None = None,
        partial_callback: Callable[[str], None] | None = None,
    ) -> "VolcanoAsrTurn":
        self.warm_up()
        turn = VolcanoAsrTurn(
            self.settings,
            self._api_key,
            self._app_id,
            self._access_token,
            timing_callback,
            partial_callback,
        )
        turn.start()
        return turn


class VolcanoAsrTurn:
    def __init__(
        self,
        settings: VolcanoAsrSettings,
        api_key: str,
        app_id: str,
        access_token: str,
        timing_callback: Callable[[str, float, dict], None] | None,
        partial_callback: Callable[[str], None] | None,
    ) -> None:
        self._settings = settings
        self._api_key = api_key
        self._app_id = app_id
        self._access_token = access_token
        self._timing_callback = timing_callback
        self._partial_callback = partial_callback
        queue_frames = max(4, int(settings.max_buffer_sec * 1000 / 20))
        self._audio_queue: queue.Queue[bytes | None] = queue.Queue(maxsize=queue_frames)
        self._ready = threading.Event()
        self._last_packet_sent = threading.Event()
        self._completed = threading.Event()
        self._aborted = threading.Event()
        self._thread = threading.Thread(target=self._thread_main, name="volcano-asr-turn", daemon=True)
        self._result = ""
        self._error: Exception | None = None
        self._started_at = time.perf_counter()
        self._finish_requested_at = 0.0
        self._last_packet_sent_at = 0.0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._websocket = None

    def start(self) -> None:
        self._thread.start()

    def feed_audio(self, pcm: bytes) -> None:
        if not pcm or self._aborted.is_set():
            return
        if self._completed.is_set():
            if self._error is not None:
                raise RuntimeError(f"Volcano ASR stopped before audio capture completed: {self._error}")
            return
        try:
            self._audio_queue.put_nowait(bytes(pcm))
        except queue.Full as exc:
            self.abort()
            raise RuntimeError("Volcano ASR audio queue exceeded its hard limit") from exc

    def finish(self, pcm: bytes) -> str:
        del pcm
        self._finish_requested_at = time.perf_counter()
        try:
            self._audio_queue.put(None, timeout=0.1)
        except queue.Full as exc:
            self.abort()
            raise RuntimeError("Volcano ASR could not enqueue its final marker") from exc
        deadline = self._settings.max_buffer_sec + self._settings.final_timeout_sec + 1.0
        if not self._ready.is_set():
            deadline += self._settings.connect_timeout_sec
        if not self._completed.wait(deadline):
            self.abort()
            raise TimeoutError("Timed out waiting for the final Volcano ASR result")
        if self._error is not None:
            raise RuntimeError(f"Volcano ASR failed: {self._error}") from self._error
        return self._result.strip()

    def abort(self) -> None:
        self._aborted.set()
        try:
            self._audio_queue.put_nowait(None)
        except queue.Full:
            pass
        if self._loop is not None and self._websocket is not None:
            try:
                asyncio.run_coroutine_threadsafe(self._websocket.close(), self._loop)
            except Exception:
                pass

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._run())
        except Exception as exc:
            self._error = exc
        finally:
            self._completed.set()

    def _headers(self) -> dict[str, str]:
        headers = {
            "X-Api-Resource-Id": self._settings.resource_id,
            "X-Api-Request-Id": str(uuid.uuid4()),
            "X-Api-Sequence": "-1",
        }
        if self._api_key:
            headers["X-Api-Key"] = self._api_key
        else:
            headers["X-Api-App-Key"] = self._app_id
            headers["X-Api-Access-Key"] = self._access_token
        return headers

    def _payload(self) -> dict[str, Any]:
        return {
            "user": {"uid": str(uuid.uuid4())},
            "audio": {
                "format": "pcm",
                "codec": "raw",
                "rate": self._settings.sample_rate,
                "bits": 16,
                "channel": 1,
            },
            "request": {
                "model_name": "bigmodel",
                "enable_itn": self._settings.enable_itn,
                "enable_punc": self._settings.enable_punc,
                "enable_ddc": self._settings.enable_ddc,
                "show_utterances": True,
                "result_type": "full",
                "enable_nonstream": self._settings.enable_nonstream,
            },
        }

    async def _run(self) -> None:
        import websockets

        self._loop = asyncio.get_running_loop()
        parameters = inspect.signature(websockets.connect).parameters
        header_argument = "additional_headers" if "additional_headers" in parameters else "extra_headers"
        options = {
            header_argument: self._headers(),
            "open_timeout": self._settings.connect_timeout_sec,
            "close_timeout": 2,
            "max_size": 4 * 1024 * 1024,
        }
        async with websockets.connect(self._settings.endpoint, **options) as websocket:
            self._websocket = websocket
            await websocket.send(build_full_request(1, self._payload()))
            initial_raw = await websocket.recv()
            if not isinstance(initial_raw, bytes):
                raise RuntimeError("Volcano ASR returned a text frame during session setup")
            initial = parse_response(initial_raw)
            if initial.code != 0:
                raise RuntimeError(f"Volcano ASR rejected the session: code={initial.code} payload={initial.payload}")
            self._ready.set()
            _notify_timing(
                self._timing_callback,
                "asr_session_ready",
                self._started_at,
                {"provider": "volcano"},
            )
            sender = asyncio.create_task(self._send_audio(websocket))
            try:
                await self._receive_results(websocket)
            finally:
                if not sender.done():
                    sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)
        self._websocket = None

    async def _send_audio(self, websocket) -> None:
        sequence = 2
        buffer = bytearray()
        while not self._aborted.is_set():
            item = await asyncio.get_running_loop().run_in_executor(None, self._audio_queue.get)
            if item is None:
                break
            buffer.extend(item)
            while len(buffer) >= self._settings.packet_bytes:
                packet = bytes(buffer[: self._settings.packet_bytes])
                del buffer[: self._settings.packet_bytes]
                await websocket.send(build_audio_request(sequence, packet))
                sequence += 1
        if self._aborted.is_set():
            return
        final_packet = bytes(buffer)
        if not final_packet:
            final_packet = b"\x00\x00" * max(1, int(self._settings.sample_rate * 0.02))
        send_started_at = time.perf_counter()
        await websocket.send(build_audio_request(sequence, final_packet, is_last=True))
        self._last_packet_sent_at = time.perf_counter()
        self._last_packet_sent.set()
        _notify_timing(
            self._timing_callback,
            "asr_last_packet_sent",
            self._finish_requested_at or send_started_at,
            {"provider": "volcano", "pcm_bytes": len(final_packet)},
        )

    async def _receive_results(self, websocket) -> None:
        latest_text = ""
        while not self._aborted.is_set():
            if (
                self._last_packet_sent.is_set()
                and time.perf_counter() - self._last_packet_sent_at > self._settings.final_timeout_sec
            ):
                raise TimeoutError("Timed out waiting for the final Volcano ASR package")
            try:
                raw = await asyncio.wait_for(websocket.recv(), timeout=0.25)
            except asyncio.TimeoutError:
                continue
            if not isinstance(raw, bytes):
                continue
            response = parse_response(raw)
            if response.code != 0:
                raise RuntimeError(f"Volcano ASR error code={response.code} payload={response.payload}")
            text, definite = extract_transcript(response.payload)
            if text:
                latest_text = text
                if self._partial_callback is not None and not response.is_last_package:
                    self._partial_callback(text)
            if response.is_last_package:
                self._result = latest_text
                _notify_timing(
                    self._timing_callback,
                    "asr_final",
                    self._finish_requested_at or self._started_at,
                    {"provider": "volcano", "text_chars": len(latest_text), "definite": definite},
                )
                return


def create_asr_provider(
    provider_name: str,
    whisper_model: str,
    whisper_device: str,
    whisper_compute_type: str,
    volcano_settings: VolcanoAsrSettings,
) -> AsrProvider:
    normalized = provider_name.strip().lower()
    if normalized in ("faster_whisper", "whisper", "local"):
        return WhisperTranscriber(whisper_model, whisper_device, whisper_compute_type)
    if normalized == "volcano":
        return VolcanoStreamingAsrProvider(volcano_settings)
    raise ValueError("asr_provider must be 'volcano' or 'faster_whisper'")


def _notify_timing(
    callback: Callable[[str, float, dict], None] | None,
    phase: str,
    started_at: float,
    fields: dict,
) -> None:
    if callback is None:
        return
    try:
        callback(phase, (time.perf_counter() - started_at) * 1000.0, fields)
    except Exception:
        pass
