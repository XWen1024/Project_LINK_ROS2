"""Volcano bidirectional WebSocket TTS with local PCM playback."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import threading
import time
import uuid
import wave
from queue import Empty, Queue

from .tts_protocols import (
    EventType,
    MsgType,
    finish_session,
    receive_message,
    start_connection,
    start_session,
    task_request,
    wait_for_event,
)

try:
    import pygame

    _HAS_PYGAME = True
except ImportError:
    pygame = None
    _HAS_PYGAME = False

try:
    import websockets

    _HAS_WEBSOCKETS = True
except ImportError:
    websockets = None
    _HAS_WEBSOCKETS = False


logger = logging.getLogger(__name__)
_WS_ENDPOINT = "wss://openspeech.bytedance.com/api/v3/tts/bidirection"


def pcm_to_wav(pcm_data: bytes, sample_rate: int) -> bytes:
    with io.BytesIO() as wav_io:
        with wave.open(wav_io, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(pcm_data)
        return wav_io.getvalue()


class VolcanoTts:
    def __init__(
        self,
        app_id: str | None = None,
        access_token: str | None = None,
        resource_id: str | None = None,
        speaker: str | None = None,
        sample_rate: int = 24000,
        enabled: bool = True,
    ) -> None:
        self._app_id = app_id or os.environ.get("VOLCANO_APP_ID", "")
        self._access_token = access_token or os.environ.get("VOLCANO_ACCESS_TOKEN", "")
        self._resource_id = resource_id or os.environ.get("VOLCANO_RESOURCE_ID", "seed-tts-2.0")
        self._speaker = speaker or os.environ.get("VOLCANO_SPEAKER", "")
        self._sample_rate = int(sample_rate)
        self._mock_mode = (
            not enabled
            or not self._app_id
            or not self._access_token
            or not self._speaker
            or not _HAS_WEBSOCKETS
            or not _HAS_PYGAME
        )
        self._mixer_ready = False
        self._audio_queue: Queue = Queue()
        self._cmd_queue: Queue = Queue()
        self._phrase_cache: dict[str, bytes] = {}
        self._stop_flag = threading.Event()
        self._is_playing = False
        self._play_lock = threading.Lock()
        self._loop = None
        self._loop_thread = None
        self._play_thread = None

        if self._mock_mode:
            logger.warning("Volcano TTS mock mode. Check env vars, pygame, and websockets for real audio.")
            return

        self._init_mixer()
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(target=self._start_loop, daemon=True)
        self._loop_thread.start()
        self._play_thread = threading.Thread(target=self._play_worker, daemon=True)
        self._play_thread.start()

    def _init_mixer(self) -> None:
        try:
            pygame.mixer.quit()
        except Exception:
            pass
        try:
            pygame.mixer.init(frequency=self._sample_rate, size=-16, channels=1, buffer=2048)
            self._mixer_ready = True
        except Exception as exc:
            logger.error("Pygame mixer init failed: %s", exc)
            self._mock_mode = True

    def _start_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._ws_manager())

    async def _ws_manager(self) -> None:
        headers = {
            "X-Api-App-Key": self._app_id,
            "X-Api-Access-Key": self._access_token,
            "X-Api-Resource-Id": self._resource_id,
            "X-Api-Connect-Id": str(uuid.uuid4()),
        }
        while True:
            try:
                async with websockets.connect(_WS_ENDPOINT, extra_headers=headers, max_size=10 * 1024 * 1024) as ws:
                    await start_connection(ws)
                    await wait_for_event(ws, MsgType.FullServerResponse, EventType.ConnectionStarted)
                    logger.info("Volcano TTS WebSocket connected")
                    while True:
                        cmd = await self._loop.run_in_executor(None, self._cmd_queue.get)
                        if cmd is None:
                            return
                        if cmd["type"] == "start":
                            await self._handle_session(ws)
                        elif cmd["type"] == "full_text":
                            await self._handle_full_text(ws, cmd["text"])
            except Exception as exc:
                logger.warning("Volcano TTS WebSocket reconnecting after error: %s", exc)
                await asyncio.sleep(2)

    def _request_payload(self, extra_params: dict | None = None) -> bytes:
        params = {
            "speaker": self._speaker,
            "audio_params": {"format": "pcm", "sample_rate": self._sample_rate},
            "additions": json.dumps({"disable_markdown_filter": True}),
        }
        if extra_params:
            params.update(extra_params)
        return json.dumps(
            {"user": {"uid": str(uuid.uuid4())}, "namespace": "BidirectionalTTS", "req_params": params},
            ensure_ascii=False,
        ).encode("utf-8")

    async def _handle_full_text(self, ws, text: str) -> None:
        session_id = str(uuid.uuid4())
        await start_session(ws, self._request_payload(), session_id)
        await wait_for_event(ws, MsgType.FullServerResponse, EventType.SessionStarted)
        await task_request(ws, self._request_payload({"text": text}), session_id)
        await finish_session(ws, session_id)
        pcm_buffer = bytearray()
        while True:
            message = await receive_message(ws)
            if message.type == MsgType.AudioOnlyServer:
                pcm_buffer.extend(message.payload)
            elif message.type == MsgType.FullServerResponse and message.event in (
                EventType.SessionFinished,
                EventType.SessionCanceled,
                EventType.SessionFailed,
            ):
                break
        if pcm_buffer:
            self._phrase_cache[text] = bytes(pcm_buffer)
            self._audio_queue.put(("audio", bytes(pcm_buffer)))
        self._audio_queue.put(("end", None))

    async def _handle_session(self, ws) -> None:
        session_id = str(uuid.uuid4())
        await start_session(ws, self._request_payload(), session_id)
        await wait_for_event(ws, MsgType.FullServerResponse, EventType.SessionStarted)

        async def recv_task() -> None:
            pcm_buffer = bytearray()
            chunk_size = int(self._sample_rate * 2 * 0.3)
            while True:
                try:
                    message = await receive_message(ws)
                except Exception:
                    self._audio_queue.put(("end", None))
                    break
                if message.type == MsgType.AudioOnlyServer:
                    pcm_buffer.extend(message.payload)
                    if len(pcm_buffer) >= chunk_size:
                        self._audio_queue.put(("audio", bytes(pcm_buffer)))
                        pcm_buffer = bytearray()
                elif message.type == MsgType.FullServerResponse and message.event in (
                    EventType.SessionFinished,
                    EventType.SessionCanceled,
                    EventType.SessionFailed,
                ):
                    if pcm_buffer:
                        self._audio_queue.put(("audio", bytes(pcm_buffer)))
                    self._audio_queue.put(("end", None))
                    break

        receiver = asyncio.create_task(recv_task())
        while True:
            cmd = await self._loop.run_in_executor(None, self._cmd_queue.get)
            if cmd is None:
                break
            if cmd["type"] == "text" and not self._stop_flag.is_set():
                await task_request(ws, self._request_payload({"text": cmd["text"]}), session_id)
                await asyncio.sleep(0.005)
            elif cmd["type"] == "end":
                if not self._stop_flag.is_set():
                    await finish_session(ws, session_id)
                break
            elif cmd["type"] == "stop":
                self._stop_flag.set()
                break
        await receiver

    def _play_worker(self) -> None:
        channel = pygame.mixer.Channel(0) if self._mixer_ready else None
        while True:
            item = self._audio_queue.get()
            if item is None:
                break
            msg_type, data = item
            if self._stop_flag.is_set():
                if channel and channel.get_busy():
                    channel.stop()
                if msg_type == "end":
                    self._is_playing = False
                continue
            if msg_type == "end":
                self._is_playing = False
                continue
            if msg_type == "audio" and self._mixer_ready and channel:
                self._is_playing = True
                if len(data) % 2:
                    data = data[:-1]
                if not data:
                    continue
                try:
                    sound = pygame.mixer.Sound(file=io.BytesIO(pcm_to_wav(data, self._sample_rate)))
                    while channel.get_queue() is not None:
                        if self._stop_flag.is_set():
                            break
                        time.sleep(0.01)
                    if self._stop_flag.is_set():
                        channel.stop()
                    elif not channel.get_busy():
                        channel.play(sound)
                    else:
                        channel.queue(sound)
                except Exception as exc:
                    logger.error("Pygame playback failed: %s", exc)

    def speak_stream_start(self) -> None:
        if self._mock_mode:
            return
        self._stop_flag.clear()
        self._is_playing = True
        self._cmd_queue.put({"type": "start"})

    def speak_stream_feed(self, text: str) -> None:
        if not text:
            return
        if self._mock_mode:
            print(text, end="", flush=True)
            return
        if not self._stop_flag.is_set():
            self._cmd_queue.put({"type": "text", "text": text})

    def speak_stream_end(self) -> None:
        if self._mock_mode:
            print(flush=True)
            return
        self._cmd_queue.put({"type": "end"})

    def speak(self, text: str) -> None:
        if not text:
            return
        if self._mock_mode:
            logger.info("[TTS mock] %s", text)
            print(f"[TTS] {text}", flush=True)
            return
        with self._play_lock:
            self.stop()
            self._stop_flag.clear()
            self._is_playing = True
            if text in self._phrase_cache:
                self._audio_queue.put(("audio", self._phrase_cache[text]))
                self._audio_queue.put(("end", None))
            elif len(text) <= 25:
                self._cmd_queue.put({"type": "full_text", "text": text})
            else:
                self._cmd_queue.put({"type": "start"})
                self._cmd_queue.put({"type": "text", "text": text})
                self._cmd_queue.put({"type": "end"})

    def stop(self) -> None:
        self._stop_flag.set()
        self._cmd_queue.put({"type": "stop"})
        if self._mixer_ready:
            channel = pygame.mixer.Channel(0)
            if channel:
                channel.stop()
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except Empty:
                break
        while not self._cmd_queue.empty():
            try:
                self._cmd_queue.get_nowait()
            except Empty:
                break
        self._is_playing = False

    def shutdown(self) -> None:
        self.stop()
        self._cmd_queue.put(None)
        self._audio_queue.put(None)
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._loop_thread:
            self._loop_thread.join(timeout=2.0)
        if self._mixer_ready:
            pygame.mixer.quit()
