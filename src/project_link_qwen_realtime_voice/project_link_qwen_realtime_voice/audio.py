"""Bounded full-duplex PCM input and output."""

from __future__ import annotations

import os
import queue
import struct
import threading
import time
from collections.abc import Callable


def resolve_device(audio, name: str, input_device: bool) -> int | None:
    wanted = name.strip().lower()
    if not wanted:
        return None
    channel_key = "maxInputChannels" if input_device else "maxOutputChannels"
    for index in range(audio.get_device_count()):
        info = audio.get_device_info_by_index(index)
        if int(info.get(channel_key, 0)) > 0 and wanted in str(info.get("name", "")).lower():
            return index
    return None


def pcm16_levels(pcm: bytes) -> tuple[int, float]:
    """Return peak and RMS for little-endian mono PCM16 without NumPy."""
    usable = len(pcm) - (len(pcm) % 2)
    if usable <= 0:
        return 0, 0.0
    peak = 0
    square_sum = 0
    count = 0
    for sample, in struct.iter_unpack("<h", pcm[:usable]):
        magnitude = abs(sample)
        peak = max(peak, magnitude)
        square_sum += sample * sample
        count += 1
    return peak, (square_sum / count) ** 0.5 if count else 0.0


class DuplexPcmAudio:
    def __init__(
        self,
        input_callback: Callable[[bytes], None],
        input_device_name: str,
        output_sink: str,
        input_sample_rate: int = 16000,
        output_sample_rate: int = 24000,
        input_chunk_ms: int = 100,
        output_chunk_ms: int = 50,
        queue_seconds: float = 30.0,
    ) -> None:
        self._input_callback = input_callback
        self._input_device_name = input_device_name
        self._output_sink = output_sink
        self._input_sample_rate = input_sample_rate
        self._output_sample_rate = output_sample_rate
        self._input_frames = max(160, input_sample_rate * input_chunk_ms // 1000)
        self._output_bytes = max(2, output_sample_rate * output_chunk_ms // 1000 * 2)
        queue_chunks = max(4, int(queue_seconds * 1000 / max(1, output_chunk_ms)))
        self._output_queue: queue.Queue[tuple[int, bytes] | None] = queue.Queue(maxsize=queue_chunks)
        self._stop = threading.Event()
        self._input_gate = threading.Event()
        self._generation = 0
        self._lock = threading.Lock()
        self._audio = None
        self._input_stream = None
        self._output_stream = None
        self._input_device_index: int | None = None
        self._resolved_input_device_name = ""
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        if self._output_sink:
            os.environ["PULSE_SINK"] = self._output_sink
        import pyaudio

        self._audio = pyaudio.PyAudio()
        input_index = resolve_device(self._audio, self._input_device_name, True)
        if self._input_device_name.strip() and input_index is None:
            raise RuntimeError(f"Audio input device not found: {self._input_device_name}")
        self._input_device_index = input_index
        if input_index is not None:
            self._resolved_input_device_name = str(
                self._audio.get_device_info_by_index(input_index).get("name", "")
            )
        self._input_stream = self._audio.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=self._input_sample_rate,
            input=True,
            input_device_index=input_index,
            frames_per_buffer=self._input_frames,
        )
        self._output_stream = self._audio.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=self._output_sample_rate,
            output=True,
            frames_per_buffer=self._output_bytes // 2,
        )
        self._threads = [
            threading.Thread(target=self._capture_loop, name="qwen-mic", daemon=True),
            threading.Thread(target=self._playback_loop, name="qwen-speaker", daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    @property
    def input_device_index(self) -> int | None:
        return self._input_device_index

    @property
    def resolved_input_device_name(self) -> str:
        return self._resolved_input_device_name

    def set_input_enabled(self, enabled: bool) -> None:
        if enabled:
            self._input_gate.set()
        else:
            self._input_gate.clear()

    def next_generation(self) -> int:
        with self._lock:
            self._generation += 1
            return self._generation

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    def enqueue(self, pcm: bytes, generation: int) -> bool:
        if not pcm or generation != self.generation:
            return False
        for offset in range(0, len(pcm), self._output_bytes):
            chunk = pcm[offset:offset + self._output_bytes]
            try:
                self._output_queue.put_nowait((generation, chunk))
            except queue.Full:
                return False
        return True

    def interrupt(self) -> int:
        generation = self.next_generation()
        while True:
            try:
                self._output_queue.get_nowait()
                self._output_queue.task_done()
            except queue.Empty:
                break
        return generation

    def wait_idle(self, timeout_sec: float) -> bool:
        deadline = time.monotonic() + max(0.0, timeout_sec)
        while time.monotonic() < deadline:
            if self._output_queue.unfinished_tasks == 0:
                return True
            time.sleep(0.01)
        return self._output_queue.unfinished_tasks == 0

    def _capture_loop(self) -> None:
        while not self._stop.is_set():
            try:
                pcm = self._input_stream.read(self._input_frames, exception_on_overflow=False)
                if self._input_gate.is_set():
                    self._input_callback(pcm)
            except Exception:
                if not self._stop.wait(0.1):
                    continue

    def _playback_loop(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._output_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if item is None:
                self._output_queue.task_done()
                return
            generation, pcm = item
            try:
                if generation == self.generation:
                    self._output_stream.write(pcm)
            finally:
                self._output_queue.task_done()

    def close(self) -> None:
        self._stop.set()
        self._input_gate.clear()
        try:
            self._output_queue.put_nowait(None)
        except queue.Full:
            pass
        for thread in self._threads:
            thread.join(timeout=1.0)
        for stream in (self._input_stream, self._output_stream):
            if stream is not None:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
        if self._audio is not None:
            self._audio.terminate()
