"""Raw microphone streaming for cloud server-VAD Volcengine S2S sessions."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class RawPcmCaptureSettings:
    sample_rate: int = 16000
    chunk_ms: int = 100
    no_speech_timeout_sec: float = 8.0
    max_utterance_sec: float = 30.0

    @property
    def frames_per_chunk(self) -> int:
        return max(1, int(self.sample_rate * self.chunk_ms / 1000))


def resolve_input_device_index(
    audio: Any,
    preferred_name: str = "",
    fallback_index: int | None = None,
) -> tuple[int | None, str]:
    """Resolve the input device on each open so USB index changes are harmless."""
    wanted = preferred_name.strip().casefold()
    if wanted:
        for index in range(int(audio.get_device_count())):
            info = audio.get_device_info_by_index(index)
            name = str(info.get("name", ""))
            if int(info.get("maxInputChannels", 0)) > 0 and wanted in name.casefold():
                return index, name
        raise RuntimeError(f"audio input device not found by name: {preferred_name}")
    if fallback_index is not None and fallback_index >= 0:
        info = audio.get_device_info_by_index(int(fallback_index))
        if int(info.get("maxInputChannels", 0)) < 1:
            raise RuntimeError(
                f"audio input index {fallback_index} has no input channels: "
                f"{info.get('name', 'unknown')}"
            )
        return int(fallback_index), str(info.get("name", "unknown"))
    return None, "default"


class ServerVadPcmRecorder:
    """Stream unprocessed PCM until the Volcengine server marks the turn complete."""

    def __init__(
        self,
        settings: RawPcmCaptureSettings,
        input_device_index: int | None = None,
        input_device_name: str = "",
        device_selected_callback: Callable[[int | None, str], None] | None = None,
    ) -> None:
        self.settings = settings
        self.input_device_index = input_device_index
        self.input_device_name = input_device_name
        self.device_selected_callback = device_selected_callback

    def record(
        self,
        server_endpoint: threading.Event,
        speech_started_ns: Callable[[], int | None],
        chunk_callback: Callable[[bytes], None],
    ) -> tuple[int, str]:
        try:
            import pyaudio
        except ImportError as exc:
            raise RuntimeError("PyAudio is required for microphone recording") from exc

        audio = pyaudio.PyAudio()
        selected_index, selected_name = resolve_input_device_index(
            audio,
            self.input_device_name,
            self.input_device_index,
        )
        if self.device_selected_callback is not None:
            self.device_selected_callback(selected_index, selected_name)
        open_kwargs: dict[str, Any] = {
            "format": pyaudio.paInt16,
            "channels": 1,
            "rate": self.settings.sample_rate,
            "input": True,
            "frames_per_buffer": self.settings.frames_per_chunk,
        }
        if selected_index is not None:
            open_kwargs["input_device_index"] = selected_index
        stream = audio.open(**open_kwargs)
        capture_started_ns = time.monotonic_ns()
        total_bytes_sent = 0
        try:
            while True:
                reason = self._stop_reason(
                    server_endpoint,
                    speech_started_ns(),
                    capture_started_ns,
                    time.monotonic_ns(),
                )
                if reason:
                    return total_bytes_sent, reason
                chunk = stream.read(self.settings.frames_per_chunk, exception_on_overflow=False)
                # If the server endpoint arrived during the blocking read, drop
                # this final local chunk instead of sending audio after THINKING.
                if server_endpoint.is_set():
                    return total_bytes_sent, "server_vad_endpoint"
                chunk_callback(chunk)
                total_bytes_sent += len(chunk)
        finally:
            stream.stop_stream()
            stream.close()
            audio.terminate()

    def _stop_reason(
        self,
        server_endpoint: threading.Event,
        speech_started_at_ns: int | None,
        capture_started_ns: int,
        now_ns: int,
    ) -> str | None:
        if server_endpoint.is_set():
            return "server_vad_endpoint"
        if speech_started_at_ns is None:
            if now_ns - capture_started_ns >= int(self.settings.no_speech_timeout_sec * 1e9):
                return "no_speech_timeout"
            return None
        if now_ns - speech_started_at_ns >= int(self.settings.max_utterance_sec * 1e9):
            return "max_utterance_timeout"
        return None
