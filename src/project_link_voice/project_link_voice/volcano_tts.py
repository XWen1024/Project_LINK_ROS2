"""Volcano bidirectional WebSocket TTS with local PCM playback."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import io
import json
import logging
import os
import threading
import time
import unicodedata
import uuid
import wave
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from typing import Callable

from .tts_protocols import (
    EventType,
    MsgType,
    cancel_session,
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


@dataclass
class _CacheEntry:
    pcm: bytes
    expires_at: float
    size: int


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
        output_device: str | None = None,
        sample_rate: int = 24000,
        enabled: bool = True,
        mixer_buffer_samples: int = 512,
        stream_audio_chunk_ms: int = 60,
        dynamic_cache_ttl_sec: float = 900.0,
        dynamic_cache_max_entries: int = 64,
        dynamic_cache_max_bytes: int = 16 * 1024 * 1024,
    ) -> None:
        self._app_id = app_id or os.environ.get("VOLCANO_APP_ID", "")
        self._access_token = access_token or os.environ.get("VOLCANO_ACCESS_TOKEN", "")
        self._resource_id = resource_id or os.environ.get("VOLCANO_RESOURCE_ID", "seed-tts-2.0")
        self._speaker = speaker or os.environ.get("VOLCANO_SPEAKER", "")
        self._output_device = output_device or os.environ.get("PROJECT_LINK_AUDIO_OUTPUT_DEVICE", "")
        self._sample_rate = int(sample_rate)
        self._mixer_buffer_samples = max(256, int(mixer_buffer_samples))
        self._stream_audio_chunk_bytes = max(2, int(self._sample_rate * 2 * stream_audio_chunk_ms / 1000))
        self._cache_ttl_sec = max(1.0, float(dynamic_cache_ttl_sec))
        self._cache_max_entries = max(1, int(dynamic_cache_max_entries))
        self._cache_max_bytes = max(1024, int(dynamic_cache_max_bytes))
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
        self._phrase_cache: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._cache_candidates: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._persistent_files: dict[str, Path] = {}
        self._cache_bytes = 0
        self._cache_lock = threading.Lock()
        self._waiting_prompt_active = threading.Event()
        self._shutdown_flag = threading.Event()
        self._stop_flag = threading.Event()
        self._generation_lock = threading.Lock()
        self._playback_generation = 0
        self._active_stream_generation = 0
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
        self._cache_thread = threading.Thread(target=self._cache_cleanup_worker, daemon=True)
        self._cache_thread.start()

    def _init_mixer(self) -> None:
        if self._output_device:
            os.environ["PULSE_SINK"] = self._output_device
            os.environ.setdefault("SDL_AUDIODRIVER", "pulseaudio")
            logger.info("Binding pygame audio output to Pulse sink: %s", self._output_device)
        try:
            pygame.mixer.quit()
        except Exception:
            pass
        try:
            pygame.mixer.init(
                frequency=self._sample_rate,
                size=-16,
                channels=1,
                buffer=self._mixer_buffer_samples,
            )
            self._mixer_ready = True
        except Exception as exc:
            logger.error("Pygame mixer init failed: %s", exc)
            self._mock_mode = True

    def _start_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._ws_manager())

    async def _ws_manager(self) -> None:
        base_headers = {
            "X-Api-App-Key": self._app_id,
            "X-Api-Access-Key": self._access_token,
            "X-Api-Resource-Id": self._resource_id,
        }
        while True:
            try:
                headers = {**base_headers, "X-Api-Connect-Id": str(uuid.uuid4())}
                parameters = inspect.signature(websockets.connect).parameters
                header_argument = (
                    "additional_headers" if "additional_headers" in parameters else "extra_headers"
                )
                options = {
                    header_argument: headers,
                    "max_size": 10 * 1024 * 1024,
                    "open_timeout": 5,
                    "close_timeout": 2,
                }
                async with websockets.connect(_WS_ENDPOINT, **options) as ws:
                    await start_connection(ws)
                    await wait_for_event(ws, MsgType.FullServerResponse, EventType.ConnectionStarted)
                    logger.info("Volcano TTS WebSocket connected")
                    while True:
                        cmd = await self._loop.run_in_executor(None, self._cmd_queue.get)
                        if cmd is None:
                            return
                        if cmd["type"] == "start":
                            self._stop_flag.clear()
                            await self._handle_session(ws, cmd)
                        elif cmd["type"] == "full_text":
                            self._stop_flag.clear()
                            await self._handle_full_text(ws, cmd)
                        elif cmd["type"] == "cache_file":
                            self._stop_flag.clear()
                            await self._handle_cache_file(ws, cmd)
                        elif cmd["type"] == "stop":
                            continue
            except Exception as exc:
                logger.warning("Volcano TTS WebSocket reconnecting after error: %s", exc)
                await asyncio.sleep(2)

    def _request_payload(self, extra_params: dict | None = None, audio_format: str = "pcm") -> bytes:
        params = {
            "speaker": self._speaker,
            "audio_params": {"format": audio_format, "sample_rate": self._sample_rate},
            "additions": json.dumps({"disable_markdown_filter": True}),
        }
        if extra_params:
            params.update(extra_params)
        return json.dumps(
            {"user": {"uid": str(uuid.uuid4())}, "namespace": "BidirectionalTTS", "req_params": params},
            ensure_ascii=False,
        ).encode("utf-8")

    async def _handle_full_text(self, ws, command: dict) -> None:
        text = command["text"]
        generation = int(command["generation"])
        requested_at = float(command["requested_at"])
        timing_callback = command.get("timing_callback")
        session_id = str(uuid.uuid4())
        await start_session(ws, self._request_payload(), session_id)
        await wait_for_event(ws, MsgType.FullServerResponse, EventType.SessionStarted)
        self._notify_timing(
            timing_callback,
            "tts_request_sent",
            requested_at,
            {"mode": "full_text"},
        )
        await task_request(ws, self._request_payload({"text": text}), session_id)
        await finish_session(ws, session_id)
        pcm_buffer = bytearray()
        playback_buffer = bytearray()
        first_audio_reported = False
        first_playback_chunk_sent = False
        final_event = None
        cancel_sent = False
        cancel_started_at = 0.0
        while True:
            if (
                (self._stop_flag.is_set() or not self._is_current_generation(generation))
                and not cancel_sent
            ):
                await cancel_session(ws, session_id)
                cancel_sent = True
                cancel_started_at = time.perf_counter()
            if cancel_sent and time.perf_counter() - cancel_started_at > 2.0:
                raise TimeoutError("Timed out canceling the previous Volcano TTS full-text session")
            try:
                message = await asyncio.wait_for(receive_message(ws), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            if message.type == MsgType.AudioOnlyServer:
                if not first_audio_reported:
                    first_audio_reported = True
                    self._notify_timing(
                        timing_callback,
                        "tts_first_audio",
                        requested_at,
                        {"mode": "full_text", "cached": False},
                    )
                pcm_buffer.extend(message.payload)
                playback_buffer.extend(message.payload)
                if not first_playback_chunk_sent:
                    self._audio_queue.put(
                        ("audio", bytes(playback_buffer), timing_callback, requested_at, True, generation)
                    )
                    playback_buffer.clear()
                    first_playback_chunk_sent = True
                while len(playback_buffer) >= self._stream_audio_chunk_bytes:
                    chunk = bytes(playback_buffer[: self._stream_audio_chunk_bytes])
                    del playback_buffer[: self._stream_audio_chunk_bytes]
                    self._audio_queue.put(("audio", chunk, None, 0.0, False, generation))
            elif message.type == MsgType.FullServerResponse and message.event in (
                EventType.SessionFinished,
                EventType.SessionCanceled,
                EventType.SessionFailed,
            ):
                final_event = message.event
                break
        if playback_buffer:
            self._audio_queue.put(("audio", bytes(playback_buffer), None, 0.0, False, generation))
        if final_event == EventType.SessionFinished and pcm_buffer:
            self._cache_admit(text, bytes(pcm_buffer))
        self._audio_queue.put(("end", None, None, 0.0, False, generation))
        self._notify_timing(
            timing_callback,
            "tts_synthesis_complete",
            requested_at,
            {
                "mode": "full_text",
                "cached": False,
                "success": final_event == EventType.SessionFinished,
                "audio_bytes": len(pcm_buffer),
            },
        )

    async def _handle_cache_file(self, ws, command: dict) -> None:
        completed = command["completed"]
        result = command["result"]
        target = Path(command["path"])
        audio_format = str(command.get("audio_format") or "mp3")
        session_id = str(uuid.uuid4())
        try:
            await start_session(ws, self._request_payload(audio_format=audio_format), session_id)
            await wait_for_event(ws, MsgType.FullServerResponse, EventType.SessionStarted)
            await task_request(
                ws,
                self._request_payload({"text": command["text"]}, audio_format=audio_format),
                session_id,
            )
            await finish_session(ws, session_id)
            audio_buffer = bytearray()
            final_event = None
            while True:
                message = await receive_message(ws)
                if message.type == MsgType.AudioOnlyServer:
                    audio_buffer.extend(message.payload)
                elif message.type == MsgType.FullServerResponse and message.event in (
                    EventType.SessionFinished,
                    EventType.SessionCanceled,
                    EventType.SessionFailed,
                ):
                    final_event = message.event
                    break
            if final_event != EventType.SessionFinished or not audio_buffer:
                result["error"] = f"TTS cache synthesis ended with event={final_event}"
                return
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + ".tmp")
            temporary.write_bytes(audio_buffer)
            os.replace(temporary, target)
            metadata_path = Path(command["metadata_path"])
            metadata_temporary = metadata_path.with_name(metadata_path.name + ".tmp")
            metadata_temporary.write_text(
                json.dumps(command["metadata"], ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(metadata_temporary, metadata_path)
            result["success"] = True
            result["audio_bytes"] = len(audio_buffer)
        except Exception as exc:
            result["error"] = str(exc)
            raise
        finally:
            completed.set()

    async def _handle_session(self, ws, command: dict) -> None:
        requested_at = float(command["requested_at"])
        timing_callback = command.get("timing_callback")
        generation = int(command["generation"])
        session_id = str(uuid.uuid4())
        await start_session(ws, self._request_payload(), session_id)
        await wait_for_event(ws, MsgType.FullServerResponse, EventType.SessionStarted)

        async def recv_task() -> None:
            pcm_buffer = bytearray()
            first_audio_reported = False
            while True:
                try:
                    message = await receive_message(ws)
                except Exception:
                    self._audio_queue.put(("end", None, None, 0.0, False, generation))
                    self._notify_timing(
                        timing_callback,
                        "tts_synthesis_complete",
                        requested_at,
                        {"mode": "stream", "success": False},
                    )
                    break
                if message.type == MsgType.AudioOnlyServer:
                    if not first_audio_reported:
                        first_audio_reported = True
                        self._notify_timing(
                            timing_callback,
                            "tts_first_audio",
                            requested_at,
                            {"mode": "stream", "cached": False},
                        )
                        self._audio_queue.put(
                            ("audio", message.payload, timing_callback, requested_at, True, generation)
                        )
                        continue
                    pcm_buffer.extend(message.payload)
                    while len(pcm_buffer) >= self._stream_audio_chunk_bytes:
                        chunk = bytes(pcm_buffer[: self._stream_audio_chunk_bytes])
                        del pcm_buffer[: self._stream_audio_chunk_bytes]
                        self._audio_queue.put(("audio", chunk, None, 0.0, False, generation))
                elif message.type == MsgType.FullServerResponse and message.event in (
                    EventType.SessionFinished,
                    EventType.SessionCanceled,
                    EventType.SessionFailed,
                ):
                    if pcm_buffer:
                        self._audio_queue.put(
                            ("audio", bytes(pcm_buffer), None, 0.0, False, generation)
                        )
                    self._audio_queue.put(("end", None, None, 0.0, False, generation))
                    self._notify_timing(
                        timing_callback,
                        "tts_synthesis_complete",
                        requested_at,
                        {
                            "mode": "stream",
                            "cached": False,
                            "success": message.event == EventType.SessionFinished,
                        },
                    )
                    break

        receiver = asyncio.create_task(recv_task())
        request_reported = False
        while True:
            cmd = await self._loop.run_in_executor(None, self._cmd_queue.get)
            if cmd is None:
                break
            if (
                cmd["type"] == "text"
                and int(cmd.get("generation", generation)) == generation
                and not self._stop_flag.is_set()
            ):
                if not request_reported:
                    request_reported = True
                    self._notify_timing(
                        timing_callback,
                        "tts_request_sent",
                        requested_at,
                        {"mode": "stream"},
                    )
                await task_request(ws, self._request_payload({"text": cmd["text"]}), session_id)
                await asyncio.sleep(0.005)
            elif cmd["type"] == "end" and int(cmd.get("generation", generation)) == generation:
                if not self._stop_flag.is_set():
                    await finish_session(ws, session_id)
                break
            elif cmd["type"] == "stop":
                self._stop_flag.set()
                await cancel_session(ws, session_id)
                break
        try:
            await asyncio.wait_for(receiver, timeout=2.0 if self._stop_flag.is_set() else None)
        except asyncio.TimeoutError as exc:
            receiver.cancel()
            await asyncio.gather(receiver, return_exceptions=True)
            raise TimeoutError("Timed out canceling the previous Volcano TTS stream session") from exc

    def _play_worker(self) -> None:
        channel = pygame.mixer.Channel(0) if self._mixer_ready else None
        while True:
            item = self._audio_queue.get()
            if item is None:
                break
            msg_type, data, timing_callback, requested_at, first_chunk, generation = item
            if not self._is_current_generation(generation):
                continue
            if self._stop_flag.is_set():
                if channel and channel.get_busy():
                    channel.stop()
                if msg_type == "end":
                    self._is_playing = False
                continue
            if msg_type == "end":
                if channel:
                    while (
                        (channel.get_busy() or channel.get_queue() is not None)
                        and not self._stop_flag.is_set()
                    ):
                        time.sleep(0.005)
                self._is_playing = False
                continue
            if msg_type in ("audio", "file") and self._mixer_ready and channel:
                self._is_playing = True
                try:
                    if msg_type == "audio":
                        if len(data) % 2:
                            data = data[:-1]
                        if not data:
                            continue
                        sound = pygame.mixer.Sound(file=io.BytesIO(pcm_to_wav(data, self._sample_rate)))
                    else:
                        sound = pygame.mixer.Sound(str(data))
                    with self._play_lock:
                        if not self._is_current_generation(generation):
                            continue
                        while channel.get_queue() is not None:
                            if self._stop_flag.is_set() or not self._is_current_generation(generation):
                                break
                            time.sleep(0.005)
                        if not self._is_current_generation(generation):
                            continue
                        if self._stop_flag.is_set():
                            channel.stop()
                        elif not channel.get_busy():
                            channel.play(sound)
                        else:
                            channel.queue(sound)
                    if first_chunk:
                        self._notify_timing(
                            timing_callback,
                            "tts_playback_started",
                            requested_at,
                            {"cached": msg_type == "file"},
                        )
                except Exception as exc:
                    logger.error("Pygame playback failed: %s", exc)

    def speak_stream_start(
        self,
        timing_callback: Callable[[str, float, dict], None] | None = None,
    ) -> None:
        if self._mock_mode:
            self._notify_immediate_tts(timing_callback, "stream", mock=True)
            return
        if not self._waiting_prompt_active.is_set():
            with self._play_lock:
                self.stop()
        self._stop_flag.clear()
        generation = self._next_generation()
        self._active_stream_generation = generation
        self._is_playing = True
        self._cmd_queue.put(
            {
                "type": "start",
                "requested_at": time.perf_counter(),
                "timing_callback": timing_callback,
                "generation": generation,
            }
        )

    def speak_stream_feed(self, text: str) -> None:
        if not text:
            return
        if self._mock_mode:
            print(text, end="", flush=True)
            return
        if not self._stop_flag.is_set():
            self._cmd_queue.put(
                {
                    "type": "text",
                    "text": text,
                    "generation": self._active_stream_generation,
                }
            )

    def speak_stream_end(self) -> None:
        if self._mock_mode:
            print(flush=True)
            return
        self._cmd_queue.put({"type": "end", "generation": self._active_stream_generation})

    def speak(
        self,
        text: str,
        timing_callback: Callable[[str, float, dict], None] | None = None,
    ) -> None:
        if not text:
            return
        if self._mock_mode:
            logger.info("[TTS mock] %s", text)
            print(f"[TTS] {text}", flush=True)
            self._notify_immediate_tts(timing_callback, "full_text", mock=True)
            return
        if not self._waiting_prompt_active.is_set():
            with self._play_lock:
                self.stop()
        self._stop_flag.clear()
        generation = self._next_generation()
        self._is_playing = True
        requested_at = time.perf_counter()
        persistent_file = self._persistent_files.get(self._normalize_cache_text(text))
        if persistent_file is not None and persistent_file.is_file():
            self._notify_timing(
                timing_callback,
                "tts_request_sent",
                requested_at,
                {"mode": "persistent_file", "cached": True},
            )
            self._audio_queue.put(
                ("file", persistent_file, timing_callback, requested_at, True, generation)
            )
            self._audio_queue.put(("end", None, None, 0.0, False, generation))
            self._notify_timing(
                timing_callback,
                "tts_first_audio",
                requested_at,
                {"mode": "persistent_file", "cached": True},
            )
            self._notify_timing(
                timing_callback,
                "tts_synthesis_complete",
                requested_at,
                {"mode": "persistent_file", "cached": True, "success": True},
            )
            return
        cached_audio = self._cache_get(text)
        if cached_audio is not None:
            self._notify_timing(
                timing_callback,
                "tts_request_sent",
                requested_at,
                {"mode": "full_text", "cached": True},
            )
            self._audio_queue.put(
                ("audio", cached_audio, timing_callback, requested_at, True, generation)
            )
            self._audio_queue.put(("end", None, None, 0.0, False, generation))
            self._notify_timing(
                timing_callback,
                "tts_first_audio",
                requested_at,
                {"mode": "full_text", "cached": True},
            )
            self._notify_timing(
                timing_callback,
                "tts_synthesis_complete",
                requested_at,
                {"mode": "full_text", "cached": True, "success": True},
            )
        else:
            self._cmd_queue.put(
                {
                    "type": "full_text",
                    "text": text,
                    "requested_at": requested_at,
                    "timing_callback": timing_callback,
                    "generation": generation,
                }
            )

    def ensure_phrase_file(
        self,
        text: str,
        path: str | Path,
        timeout_sec: float = 20.0,
        audio_format: str = "mp3",
    ) -> bool:
        target = Path(path).expanduser()
        metadata_path = target.with_name(target.name + ".metadata.json")
        metadata = {
            "text": text,
            "speaker": self._speaker,
            "resource_id": self._resource_id,
            "sample_rate": self._sample_rate,
            "audio_format": audio_format,
        }
        if (
            target.is_file()
            and target.stat().st_size > 128
            and self._metadata_matches(metadata_path, metadata)
        ):
            self._persistent_files[self._normalize_cache_text(text)] = target
            return True
        if self._mock_mode or not text:
            return False
        completed = threading.Event()
        result: dict[str, object] = {"success": False}
        self._cmd_queue.put(
            {
                "type": "cache_file",
                "text": text,
                "path": str(target),
                "audio_format": audio_format,
                "metadata_path": str(metadata_path),
                "metadata": metadata,
                "completed": completed,
                "result": result,
            }
        )
        if not completed.wait(max(0.1, timeout_sec)):
            logger.error("Timed out caching TTS phrase to %s", target)
            return False
        if not bool(result.get("success")):
            logger.error("Failed caching TTS phrase to %s: %s", target, result.get("error", "unknown error"))
            return False
        self._persistent_files[self._normalize_cache_text(text)] = target
        logger.info("Cached TTS phrase to %s (%s bytes)", target, result.get("audio_bytes", 0))
        return True

    def prepare_persistent_phrases(
        self,
        phrases: list[str],
        directory: str | Path,
        timeout_sec: float = 20.0,
    ) -> int:
        target_dir = Path(directory).expanduser()
        ready = 0
        for phrase in phrases:
            normalized = self._normalize_cache_text(phrase)
            if not normalized:
                continue
            existing = self._persistent_files.get(normalized)
            if existing is not None and existing.is_file():
                ready += 1
                continue
            digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
            if self.ensure_phrase_file(
                phrase,
                target_dir / f"{digest}.mp3",
                timeout_sec=timeout_sec,
                audio_format="mp3",
            ):
                ready += 1
        return ready

    def play_file_blocking(self, path: str | Path, timeout_sec: float = 5.0) -> bool:
        target = Path(path).expanduser()
        if self._mock_mode or not target.is_file() or target.stat().st_size <= 128:
            return False
        with self._play_lock:
            self.stop()
            self._stop_flag.clear()
            channel = pygame.mixer.Channel(0)
            try:
                sound = pygame.mixer.Sound(str(target))
                self._is_playing = True
                channel.play(sound)
                deadline = time.monotonic() + max(0.1, timeout_sec)
                while channel.get_busy() and not self._stop_flag.is_set() and time.monotonic() < deadline:
                    time.sleep(0.01)
                completed = not channel.get_busy()
                if not completed:
                    channel.stop()
                return completed
            except Exception as exc:
                logger.error("Cached audio playback failed for %s: %s", target, exc)
                return False
            finally:
                self._is_playing = False

    def play_file_if_idle_blocking(self, path: str | Path, timeout_sec: float = 2.0) -> bool:
        target = Path(path).expanduser()
        if self._mock_mode or not target.is_file() or target.stat().st_size <= 128:
            return False
        with self._play_lock:
            channel = pygame.mixer.Channel(0)
            if channel.get_busy() or channel.get_queue() is not None:
                return False
            try:
                self._waiting_prompt_active.set()
                sound = pygame.mixer.Sound(str(target))
                self._is_playing = True
                channel.play(sound)
                deadline = time.monotonic() + max(0.1, timeout_sec)
                while channel.get_busy() and time.monotonic() < deadline:
                    time.sleep(0.005)
                completed = not channel.get_busy()
                if not completed:
                    channel.stop()
                return completed
            except Exception as exc:
                logger.error("Idle cached audio playback failed for %s: %s", target, exc)
                return False
            finally:
                self._waiting_prompt_active.clear()
                self._is_playing = False

    def wait_until_idle(self, timeout_sec: float = 5.0) -> bool:
        deadline = time.monotonic() + max(0.1, timeout_sec)
        while self._is_playing and time.monotonic() < deadline:
            time.sleep(0.01)
        return not self._is_playing

    @staticmethod
    def _normalize_cache_text(text: str) -> str:
        return " ".join(unicodedata.normalize("NFKC", text).strip().split())

    def _next_generation(self) -> int:
        with self._generation_lock:
            self._playback_generation += 1
            return self._playback_generation

    def _is_current_generation(self, generation: int) -> bool:
        with self._generation_lock:
            return int(generation) == self._playback_generation

    def _cache_get(self, text: str) -> bytes | None:
        key = self._normalize_cache_text(text)
        now = time.monotonic()
        with self._cache_lock:
            entry = self._phrase_cache.get(key)
            if entry is not None and entry.expires_at <= now:
                self._cache_bytes -= entry.size
                del self._phrase_cache[key]
                entry = None
            if entry is not None:
                self._phrase_cache.move_to_end(key)
                return entry.pcm
            candidate = self._cache_candidates.pop(key, None)
            if candidate is None or candidate.expires_at <= now:
                return None
            self._phrase_cache[key] = candidate
            self._cache_bytes += candidate.size
            while (
                len(self._phrase_cache) > self._cache_max_entries
                or self._cache_bytes > self._cache_max_bytes
            ):
                _old_key, old_entry = self._phrase_cache.popitem(last=False)
                self._cache_bytes -= old_entry.size
            return candidate.pcm

    def _cache_put(self, text: str, pcm: bytes) -> None:
        key = self._normalize_cache_text(text)
        if not key or not pcm or len(pcm) > self._cache_max_bytes:
            return
        with self._cache_lock:
            previous = self._phrase_cache.pop(key, None)
            if previous is not None:
                self._cache_bytes -= previous.size
            entry = _CacheEntry(pcm, time.monotonic() + self._cache_ttl_sec, len(pcm))
            self._phrase_cache[key] = entry
            self._cache_bytes += entry.size
            while (
                len(self._phrase_cache) > self._cache_max_entries
                or self._cache_bytes > self._cache_max_bytes
            ):
                _old_key, old_entry = self._phrase_cache.popitem(last=False)
                self._cache_bytes -= old_entry.size

    def _cache_admit(self, text: str, pcm: bytes) -> None:
        key = self._normalize_cache_text(text)
        if not key or not pcm or len(pcm) > self._cache_max_bytes:
            return
        now = time.monotonic()
        with self._cache_lock:
            self._cache_candidates[key] = _CacheEntry(
                pcm,
                now + self._cache_ttl_sec,
                len(pcm),
            )
            self._cache_candidates.move_to_end(key)
            while len(self._cache_candidates) > self._cache_max_entries:
                self._cache_candidates.popitem(last=False)

    def _cache_cleanup_worker(self) -> None:
        while not self._shutdown_flag.wait(60.0):
            now = time.monotonic()
            with self._cache_lock:
                expired_cache = [key for key, entry in self._phrase_cache.items() if entry.expires_at <= now]
                for key in expired_cache:
                    entry = self._phrase_cache.pop(key)
                    self._cache_bytes -= entry.size
                expired_candidates = [key for key, entry in self._cache_candidates.items() if entry.expires_at <= now]
                for key in expired_candidates:
                    del self._cache_candidates[key]

    @staticmethod
    def _metadata_matches(path: Path, expected: dict) -> bool:
        try:
            return json.loads(path.read_text(encoding="utf-8")) == expected
        except (OSError, ValueError, TypeError):
            return False

    @classmethod
    def _notify_immediate_tts(
        cls,
        callback: Callable[[str, float, dict], None] | None,
        mode: str,
        mock: bool,
    ) -> None:
        started_at = time.perf_counter()
        fields = {"mode": mode, "cached": False, "mock": mock, "success": True}
        cls._notify_timing(callback, "tts_request_sent", started_at, fields)
        cls._notify_timing(callback, "tts_first_audio", started_at, fields)
        cls._notify_timing(callback, "tts_synthesis_complete", started_at, fields)

    @staticmethod
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

    def stop(self) -> None:
        self._stop_flag.set()
        self._next_generation()
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
        self._cmd_queue.put({"type": "stop"})
        self._is_playing = False

    def shutdown(self) -> None:
        self._shutdown_flag.set()
        self.stop()
        self._cmd_queue.put(None)
        self._audio_queue.put(None)
        if self._loop_thread:
            self._loop_thread.join(timeout=3.0)
            if self._loop_thread.is_alive() and self._loop and self._loop.is_running():
                self._loop.call_soon_threadsafe(self._loop.stop)
                self._loop_thread.join(timeout=1.0)
        if self._play_thread:
            self._play_thread.join(timeout=1.0)
        if getattr(self, "_cache_thread", None):
            self._cache_thread.join(timeout=1.0)
        if self._mixer_ready:
            pygame.mixer.quit()
