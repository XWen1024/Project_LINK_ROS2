"""FunASR VAD endpointing with bounded recording for noisy environments."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import time
from typing import Any, Callable, Iterable


DEFAULT_IFLYTEK_INPUT_NAME = "XFM-DP-V0.0.18"


def resolve_pyaudio_input_device(
    audio: Any,
    configured_index: int | None,
    configured_name: str | None,
    sample_rate: int,
) -> tuple[int | None, str]:
    if configured_index is not None and configured_index >= 0:
        info = audio.get_device_info_by_index(configured_index)
        if int(info.get("maxInputChannels", 0)) <= 0:
            raise RuntimeError(
                f"PyAudio device {configured_index} is not an input device: {info.get('name', 'unknown')}"
            )
        return configured_index, str(info.get("name", f"device-{configured_index}"))

    name = str(configured_name or "").strip().casefold()
    if not name:
        try:
            info = audio.get_default_input_device_info()
        except Exception as exc:
            raise RuntimeError("No default PyAudio input device is available") from exc
        return int(info["index"]), str(info.get("name", "default input"))

    candidates: list[tuple[int, int, str]] = []
    available_inputs: list[str] = []
    for index in range(audio.get_device_count()):
        info = audio.get_device_info_by_index(index)
        input_channels = int(info.get("maxInputChannels", 0))
        if input_channels <= 0:
            continue
        device_name = str(info.get("name", f"device-{index}"))
        available_inputs.append(f"{index}:{device_name}")
        if name not in device_name.casefold():
            continue
        score = input_channels
        if "(hw:" in device_name.casefold():
            score += 100
        if int(float(info.get("defaultSampleRate", 0))) == int(sample_rate):
            score += 20
        candidates.append((score, index, device_name))

    if not candidates:
        devices = ", ".join(available_inputs) or "none"
        raise RuntimeError(
            f"PyAudio input containing {configured_name!r} was not found; available inputs: {devices}"
        )
    _score, index, device_name = max(candidates)
    return index, device_name


@dataclass(frozen=True)
class VadSettings:
    sample_rate: int = 16000
    capture_frame_ms: int = 20
    chunk_ms: int = 200
    pre_roll_ms: int = 400
    end_silence_ms: int = 500
    no_speech_timeout_sec: float = 8.0
    max_utterance_sec: float = 12.0
    min_speech_sec: float = 0.30

    @property
    def chunk_bytes(self) -> int:
        return int(self.sample_rate * self.chunk_ms / 1000) * 2

    @property
    def capture_frame_bytes(self) -> int:
        return int(self.sample_rate * self.capture_frame_ms / 1000) * 2

    @property
    def pre_roll_chunks(self) -> int:
        return max(1, int(self.pre_roll_ms / self.chunk_ms))


class VadEndpointState:
    """Model-agnostic VAD event state machine, intentionally independent of RMS."""

    def __init__(self, settings: VadSettings) -> None:
        self.settings = settings
        self.reset()

    def reset(self) -> None:
        self.started = False
        self.finished = False
        self.elapsed_ms = 0
        self.speech_ms = 0
        self.last_speech_end_ms: int | None = None
        self._pre_roll: deque[bytes] = deque(maxlen=self.settings.pre_roll_chunks)
        self._audio: list[bytes] = []

    def feed(self, chunk: bytes, events: Iterable[tuple[int, int]]) -> str | None:
        """Feed one PCM chunk; returns a terminal reason or ``None``."""
        self.elapsed_ms += self.settings.chunk_ms
        if not self.started:
            self._pre_roll.append(chunk)
        starts = any(start >= 0 for start, _ in events)
        ends = any(end >= 0 for _, end in events)
        end_times = [end for _, end in events if end >= 0]
        if end_times:
            self.last_speech_end_ms = max(end_times)
        started_now = starts and not self.started
        if started_now:
            self.started = True
            self._audio.extend(self._pre_roll)
            self._pre_roll.clear()
        if self.started:
            if not started_now:
                self._audio.append(chunk)
            self.speech_ms += self.settings.chunk_ms
        if self.started and ends and self.speech_ms >= int(self.settings.min_speech_sec * 1000):
            self.finished = True
            return "vad_end"
        if not self.started and self.elapsed_ms >= int(self.settings.no_speech_timeout_sec * 1000):
            self.finished = True
            return "no_speech_timeout"
        if self.elapsed_ms >= int(self.settings.max_utterance_sec * 1000):
            self.finished = True
            return "max_utterance_timeout"
        return None

    @property
    def audio(self) -> bytes:
        return b"".join(self._audio)


def extract_vad_events(result: Any) -> list[tuple[int, int]]:
    """Extract [start_ms, end_ms] pairs from common FunASR generate() results."""
    events: list[tuple[int, int]] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if "value" in value:
                visit(value["value"])
            else:
                for item in value.values():
                    visit(item)
        elif isinstance(value, (list, tuple)):
            if len(value) == 2 and all(isinstance(item, (int, float)) for item in value):
                events.append((int(value[0]), int(value[1])))
            else:
                for item in value:
                    visit(item)

    visit(result)
    return events


class FunVadRecorder:
    """Captures 16 kHz PCM and terminates only on VAD or an explicit hard bound."""

    def __init__(
        self,
        settings: VadSettings,
        model_name: str,
        device: str = "cuda",
        input_device_index: int | None = None,
        input_device_name: str | None = DEFAULT_IFLYTEK_INPUT_NAME,
    ) -> None:
        self.settings = settings
        self.model_name = model_name
        self.device = device
        self.input_device_index = input_device_index
        self.input_device_name = input_device_name
        self.selected_input_device: str | None = None
        self._model: Any = None
        self.last_speech_end_delay_ms: float | None = None

    def _model_instance(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            from funasr import AutoModel
        except ImportError as exc:
            raise RuntimeError("FunASR is not installed; install requirements-orin.txt on Orin") from exc
        try:
            self._model = AutoModel(model=self.model_name, device=self.device, disable_update=True)
        except Exception:
            if self.device == "cpu":
                raise
            self._model = AutoModel(model=self.model_name, device="cpu", disable_update=True)
        return self._model

    def warm_up(self) -> None:
        self._model_instance()

    def _generate_events(
        self,
        model: Any,
        chunk: bytes,
        cache: dict[str, Any],
        is_final: bool,
    ) -> list[tuple[int, int]]:
        import numpy as np

        waveform = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0
        result = model.generate(
            input=waveform,
            cache=cache,
            is_final=is_final,
            chunk_size=self.settings.chunk_ms,
            max_end_silence_time=self.settings.end_silence_ms,
            disable_pbar=True,
        )
        return extract_vad_events(result)

    def record(self, audio_callback: Callable[[bytes], None] | None = None) -> tuple[bytes, str]:
        try:
            import pyaudio
        except ImportError as exc:
            raise RuntimeError("PyAudio is required for microphone recording") from exc

        model = self._model_instance()
        state = VadEndpointState(self.settings)
        cache: dict[str, Any] = {}
        vad_buffer = bytearray()
        self.last_speech_end_delay_ms = None
        audio = pyaudio.PyAudio()
        selected_index, selected_name = resolve_pyaudio_input_device(
            audio,
            self.input_device_index,
            self.input_device_name,
            self.settings.sample_rate,
        )
        self.selected_input_device = selected_name
        open_kwargs = {
            "format": pyaudio.paInt16,
            "channels": 1,
            "rate": self.settings.sample_rate,
            "input": True,
            "frames_per_buffer": self.settings.capture_frame_bytes // 2,
        }
        if selected_index is not None:
            open_kwargs["input_device_index"] = selected_index
        stream = audio.open(**open_kwargs)
        capture_started_at = time.perf_counter()
        try:
            while True:
                capture_frame = stream.read(
                    self.settings.capture_frame_bytes // 2,
                    exception_on_overflow=False,
                )
                if audio_callback is not None:
                    audio_callback(capture_frame)
                vad_buffer.extend(capture_frame)
                if len(vad_buffer) < self.settings.chunk_bytes:
                    continue
                chunk = bytes(vad_buffer[: self.settings.chunk_bytes])
                del vad_buffer[: self.settings.chunk_bytes]
                next_elapsed_ms = state.elapsed_ms + self.settings.chunk_ms
                active_limit_ms = int(
                    (self.settings.max_utterance_sec if state.started else self.settings.no_speech_timeout_sec)
                    * 1000
                )
                events = self._generate_events(
                    model,
                    chunk,
                    cache,
                    is_final=next_elapsed_ms >= active_limit_ms,
                )
                reason = state.feed(chunk, events)
                if reason:
                    terminal_at = time.perf_counter()
                    if state.last_speech_end_ms is not None:
                        estimated_end = capture_started_at + state.last_speech_end_ms / 1000.0
                        self.last_speech_end_delay_ms = max(0.0, (terminal_at - estimated_end) * 1000.0)
                    return state.audio, reason
        finally:
            stream.stop_stream()
            stream.close()
            audio.terminate()
