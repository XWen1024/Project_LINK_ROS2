"""FunASR VAD endpointing with bounded recording for noisy environments."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class VadSettings:
    sample_rate: int = 16000
    chunk_ms: int = 200
    pre_roll_ms: int = 400
    no_speech_timeout_sec: float = 8.0
    max_utterance_sec: float = 12.0
    min_speech_sec: float = 0.30

    @property
    def chunk_bytes(self) -> int:
        return int(self.sample_rate * self.chunk_ms / 1000) * 2

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
        self._pre_roll: deque[bytes] = deque(maxlen=self.settings.pre_roll_chunks)
        self._audio: list[bytes] = []

    def feed(self, chunk: bytes, events: Iterable[tuple[int, int]]) -> str | None:
        """Feed one PCM chunk; returns a terminal reason or ``None``."""
        self.elapsed_ms += self.settings.chunk_ms
        if not self.started:
            self._pre_roll.append(chunk)
        starts = any(start >= 0 for start, _ in events)
        ends = any(end >= 0 for _, end in events)
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

    def __init__(self, settings: VadSettings, model_name: str, device: str = "cuda") -> None:
        self.settings = settings
        self.model_name = model_name
        self.device = device
        self._model: Any = None

    def _model_instance(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            from funasr import AutoModel
        except ImportError as exc:
            raise RuntimeError("FunASR is not installed; install requirements-orin.txt on Orin") from exc
        try:
            self._model = AutoModel(model=self.model_name, device=self.device)
        except Exception:
            if self.device == "cpu":
                raise
            self._model = AutoModel(model=self.model_name, device="cpu")
        return self._model

    def record(self) -> tuple[bytes, str]:
        try:
            import pyaudio
        except ImportError as exc:
            raise RuntimeError("PyAudio is required for microphone recording") from exc

        model = self._model_instance()
        state = VadEndpointState(self.settings)
        cache: dict[str, Any] = {}
        audio = pyaudio.PyAudio()
        stream = audio.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=self.settings.sample_rate,
            input=True,
            frames_per_buffer=self.settings.chunk_bytes // 2,
        )
        try:
            while True:
                chunk = stream.read(self.settings.chunk_bytes // 2, exception_on_overflow=False)
                result = model.generate(input=chunk, cache=cache, is_final=False)
                reason = state.feed(chunk, extract_vad_events(result))
                if reason:
                    return state.audio, reason
        finally:
            stream.stop_stream()
            stream.close()
            audio.terminate()