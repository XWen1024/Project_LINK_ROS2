#!/usr/bin/env python3
"""Local wake/microphone/speaker integration for Volcengine WebSocket S2S."""

from __future__ import annotations

import json
import os
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from .funvad import FunVadRecorder, VadSettings
from .voice_debug import VoiceDebugSink, VoiceTrace
from .volc_s2s_bridge import (
    EVT_AUDIO,
    EVT_CONTROL,
    EVT_MESSAGE,
    BridgeFrame,
    VolcS2SBridgeProcess,
)
from .wakeup import SerialWakeDetector, resolve_wakeup_serial_port


@dataclass
class TurnState:
    trace: VoiceTrace
    wake_ns: int
    done: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    first_input_ns: int | None = None
    last_input_ns: int | None = None
    commit_ns: int | None = None
    server_speech_started_ns: int | None = None
    server_speech_stopped_ns: int | None = None
    function_call_ns: int | None = None
    function_args_done_ns: int | None = None
    response_created_ns: int | None = None
    first_ai_audio_ns: int | None = None
    first_speaker_write_ns: int | None = None
    response_audio_done_ns: int | None = None
    response_done_ns: int | None = None
    audio_bytes: int = 0
    response_status: str = ""


class PcmPlaybackWorker:
    def __init__(
        self,
        sample_rate: int,
        output_device_index: int | None,
        pulse_sink: str,
        first_write_callback: Callable[[str, int, int], None],
        error_callback: Callable[[str], None],
    ) -> None:
        self._sample_rate = int(sample_rate)
        self._output_device_index = output_device_index
        self._pulse_sink = pulse_sink.strip()
        self._first_write_callback = first_write_callback
        self._error_callback = error_callback
        self._queue: queue.Queue[tuple[str, int, bytes] | None] = queue.Queue(maxsize=256)
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._ready_error: str | None = None
        self._seen_turns: set[str] = set()
        self._writing = threading.Event()
        self._thread = threading.Thread(target=self._run, name="volc-s2s-speaker", daemon=True)
        self._thread.start()

    def wait_ready(self, timeout_sec: float) -> bool:
        return self._ready.wait(max(0.0, timeout_sec)) and self._ready_error is None

    @property
    def ready_error(self) -> str | None:
        return self._ready_error

    def enqueue(self, trace_id: str, received_ns: int, pcm: bytes) -> None:
        try:
            self._queue.put_nowait((trace_id, received_ns, bytes(pcm)))
        except queue.Full:
            self._error_callback("S2S speaker queue is full; dropping one PCM frame")

    def _run(self) -> None:
        audio = None
        stream = None
        try:
            if self._pulse_sink:
                os.environ["PULSE_SINK"] = self._pulse_sink
            import pyaudio

            audio = pyaudio.PyAudio()
            open_kwargs: dict[str, Any] = {
                "format": pyaudio.paInt16,
                "channels": 1,
                "rate": self._sample_rate,
                "output": True,
                "frames_per_buffer": max(160, self._sample_rate // 10),
            }
            if self._output_device_index is not None and self._output_device_index >= 0:
                open_kwargs["output_device_index"] = self._output_device_index
            stream = audio.open(**open_kwargs)
        except Exception as exc:
            self._ready_error = f"PyAudio speaker open failed: {exc}"
            self._error_callback(self._ready_error)
            self._ready.set()
            if audio is not None:
                audio.terminate()
            return

        self._ready.set()
        try:
            while not self._stop.is_set():
                item = self._queue.get()
                if item is None:
                    break
                trace_id, received_ns, pcm = item
                write_ns = time.monotonic_ns()
                if trace_id not in self._seen_turns:
                    self._seen_turns.add(trace_id)
                    self._first_write_callback(trace_id, received_ns, write_ns)
                try:
                    self._writing.set()
                    stream.write(pcm, exception_on_underflow=False)
                except Exception as exc:
                    self._error_callback(f"S2S speaker write failed: {exc}")
                    break
                finally:
                    self._writing.clear()
                    self._queue.task_done()
        finally:
            if stream is not None:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
            if audio is not None:
                audio.terminate()

    def close(self) -> None:
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        self._thread.join(timeout=2.0)

    def wait_idle(self, timeout_sec: float) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout_sec))
        while time.monotonic() < deadline:
            if self._queue.unfinished_tasks == 0 and not self._writing.is_set():
                return True
            time.sleep(0.01)
        return self._queue.unfinished_tasks == 0 and not self._writing.is_set()


class VolcS2SVoiceNode(Node):
    def __init__(self) -> None:
        super().__init__("volc_s2s_voice_node")
        self._declare_parameters()
        self._debug_sink = VoiceDebugSink(
            self.get_logger(),
            debug_enabled=bool(self.get_parameter("debug_logging_enabled").value),
            timing_enabled=bool(self.get_parameter("timing_debug_enabled").value),
            debug_log_file=str(self.get_parameter("debug_log_file").value),
            timing_log_file=str(self.get_parameter("timing_log_file").value),
            timing_console_enabled=bool(self.get_parameter("timing_console_enabled").value),
        )
        self._status_pub = self.create_publisher(String, "/voice_s2s/status", 10)
        self._stop = threading.Event()
        self._turn_lock = threading.Lock()
        self._active_turn: TurnState | None = None
        self._session_model = "unknown"
        self._startup_trace = self._debug_sink.start_trace("volc_s2s_startup")

        output_index = int(self.get_parameter("audio_output_device_index").value)
        self._player = PcmPlaybackWorker(
            sample_rate=int(self.get_parameter("audio_sample_rate").value),
            output_device_index=output_index if output_index >= 0 else None,
            pulse_sink=str(self.get_parameter("pulse_sink").value),
            first_write_callback=self._on_first_speaker_write,
            error_callback=self.get_logger().error,
        )

        executable = str(self.get_parameter("native_bridge_executable").value)
        self._bridge = VolcS2SBridgeProcess(
            executable,
            self._on_bridge_frame,
            self.get_logger().error,
        )
        self._bridge.start()

        self._audio_thread = threading.Thread(
            target=self._audio_loop,
            name="volc-s2s-audio-loop",
            daemon=True,
        )
        self._audio_thread.start()
        self.get_logger().warning(
            "Volcengine S2S pure voice mode: local wake + mic + cloud S2S + local speaker; no cmd_vel publisher."
        )
        self.get_logger().info(
            "Timing JSONL: " + str(Path(str(self.get_parameter("timing_log_file").value)).expanduser())
        )

    def _declare_parameters(self) -> None:
        self.declare_parameter(
            "native_bridge_executable",
            os.environ.get(
                "PROJECT_LINK_VOLC_BRIDGE_BIN",
                "/home/wte/wheeltec_robot/experiments/volc_s2s_smoke/build/volc_ws_bridge",
            ),
        )
        self.declare_parameter("bridge_connect_timeout_sec", 30.0)
        self.declare_parameter("response_timeout_sec", 45.0)
        self.declare_parameter("keyboard_wakeup", False)
        self.declare_parameter("wakeup_serial_port", "auto")
        self.declare_parameter("wakeup_serial_baud", 115200)
        self.declare_parameter("wakeup_match_text", "aiui_event")
        self.declare_parameter("wakeup_serial_max_buffer_bytes", 16384)
        self.declare_parameter("wakeup_log_raw", False)
        self.declare_parameter("wakeup_ack_cache_file", "~/.cache/project_link_voice/wakeup_ack.mp3")
        self.declare_parameter("wakeup_ack_playback_timeout_sec", 5.0)
        self.declare_parameter("funvad_model", os.environ.get("PROJECT_LINK_FUNVAD_MODEL", "fsmn-vad"))
        self.declare_parameter("funvad_device", "cuda")
        self.declare_parameter("audio_sample_rate", 16000)
        self.declare_parameter("audio_chunk_ms", 200)
        self.declare_parameter("audio_pre_roll_ms", 400)
        self.declare_parameter("audio_no_speech_timeout_sec", 8.0)
        self.declare_parameter("audio_max_utterance_sec", 12.0)
        self.declare_parameter("audio_min_speech_sec", 0.30)
        self.declare_parameter("audio_input_device_index", 0)
        self.declare_parameter("audio_output_device_index", -1)
        self.declare_parameter(
            "pulse_sink",
            "alsa_output.usb-C-Media_Electronics_Inc._USB_Audio_Device-00.analog-stereo",
        )
        self.declare_parameter("post_response_quiet_sec", 0.25)
        self.declare_parameter("debug_logging_enabled", True)
        self.declare_parameter("timing_debug_enabled", True)
        self.declare_parameter("timing_console_enabled", True)
        self.declare_parameter("debug_log_file", "~/.ros/project_link_voice/voice_debug.jsonl")
        self.declare_parameter("timing_log_file", "~/.ros/project_link_voice/voice_timing.jsonl")

    def _current_turn(self) -> TurnState | None:
        with self._turn_lock:
            return self._active_turn

    def _set_active_turn(self, turn: TurnState | None) -> None:
        with self._turn_lock:
            self._active_turn = turn

    @staticmethod
    def _mark_at(trace: VoiceTrace, name: str, timestamp_ns: int, **fields: Any) -> None:
        trace.mark_at(name, timestamp_ns / 1_000_000_000.0, **fields)

    def _record_turn_interval(
        self,
        turn: TurnState,
        phase: str,
        start_ns: int | None,
        end_ns: int | None,
        **fields: Any,
    ) -> None:
        if start_ns is None or end_ns is None or end_ns < start_ns:
            return
        turn.trace.record(phase, (end_ns - start_ns) / 1_000_000.0, **fields)

    def _on_bridge_frame(self, frame: BridgeFrame) -> None:
        if frame.message_type == EVT_AUDIO:
            self._handle_audio_frame(frame)
        elif frame.message_type == EVT_MESSAGE:
            self._handle_server_message(frame)
        elif frame.message_type == EVT_CONTROL:
            self._handle_control(frame)

    def _handle_control(self, frame: BridgeFrame) -> None:
        try:
            value = json.loads(frame.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(value, dict):
            return
        event = str(value.get("event", "unknown"))
        if event == "device_registration":
            self._startup_trace.record(
                "volc_device_registration",
                float(value.get("elapsed_ms", 0.0)),
                result=value.get("result"),
                sdk_version=value.get("sdk_version"),
                sdk_commit=value.get("sdk_commit"),
            )
        elif event == "sdk_event":
            if value.get("connected") is True:
                self._startup_trace.record(
                    "volc_ws_connect",
                    float(value.get("connect_ms", 0.0)),
                    transport="websocket_low_load",
                )
                self._startup_trace.complete("connected")
                self._status("connected")
            else:
                self._status("disconnected")
        elif event == "conversation_status":
            turn = self._current_turn()
            if turn is not None:
                turn.trace.debug(
                    "volc_conversation_status",
                    status=value.get("status"),
                    name=value.get("name"),
                )
        elif event == "command_result":
            turn = self._current_turn()
            if turn is not None:
                turn.trace.debug(
                    "volc_command_result",
                    command=value.get("command"),
                    result=value.get("result"),
                )
        elif event in {"fatal", "protocol_error"}:
            self.get_logger().error(f"Volcengine bridge event: {value}")

    def _handle_server_message(self, frame: BridgeFrame) -> None:
        try:
            root = json.loads(frame.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(root, dict):
            return
        event_type = str(root.get("type", "unknown"))
        if event_type == "session.created":
            session = root.get("session") if isinstance(root.get("session"), dict) else {}
            self._session_model = str(session.get("model", "unknown"))
            self.get_logger().info(f"Volcengine S2S session model: {self._session_model}")
            return

        turn = self._current_turn()
        if turn is None:
            return
        event_ns = frame.monotonic_ns
        turn.trace.debug(
            "volc_server_event",
            event_type=event_type,
            event_id=root.get("event_id"),
            response_id=root.get("response_id"),
        )
        with turn.lock:
            if event_type == "input_audio_buffer.speech_started" and turn.server_speech_started_ns is None:
                turn.server_speech_started_ns = event_ns
                self._record_turn_interval(
                    turn,
                    "volc_first_input_to_speech_started",
                    turn.first_input_ns,
                    event_ns,
                )
            elif event_type == "input_audio_buffer.speech_stopped" and turn.server_speech_stopped_ns is None:
                turn.server_speech_stopped_ns = event_ns
                self._record_turn_interval(
                    turn,
                    "volc_last_input_to_speech_stopped",
                    turn.last_input_ns,
                    event_ns,
                )
            elif event_type == "input_audio_buffer.committed":
                self._record_turn_interval(
                    turn,
                    "volc_commit_to_server_ack",
                    turn.commit_ns,
                    event_ns,
                )
            elif event_type == "response.created" and turn.response_created_ns is None:
                turn.response_created_ns = event_ns
                self._record_turn_interval(
                    turn,
                    "volc_last_input_to_response_created",
                    turn.last_input_ns,
                    event_ns,
                )
            elif event_type in {"conversation.item.created", "response.output_item.done"}:
                item = root.get("item") if isinstance(root.get("item"), dict) else {}
                if item.get("type") == "function_call" and turn.function_call_ns is None:
                    turn.function_call_ns = event_ns
                    self._record_turn_interval(
                        turn,
                        "volc_vad_stop_to_function_call",
                        turn.server_speech_stopped_ns,
                        event_ns,
                        function=item.get("name"),
                    )
                    self._record_turn_interval(
                        turn,
                        "volc_last_input_to_function_call",
                        turn.last_input_ns,
                        event_ns,
                        function=item.get("name"),
                    )
                    turn.trace.debug(
                        "volc_function_call_received",
                        function=item.get("name"),
                        call_id=item.get("call_id"),
                    )
            elif event_type == "response.function_call_arguments.done":
                turn.function_args_done_ns = event_ns
                self._record_turn_interval(
                    turn,
                    "volc_function_call_to_arguments_done",
                    turn.function_call_ns,
                    event_ns,
                    function=root.get("name"),
                )
            elif event_type == "response.audio.done":
                turn.response_audio_done_ns = event_ns
                self._record_turn_interval(
                    turn,
                    "volc_first_audio_to_audio_done",
                    turn.first_ai_audio_ns,
                    event_ns,
                )
            elif event_type == "response.done":
                turn.response_done_ns = event_ns
                response = root.get("response") if isinstance(root.get("response"), dict) else {}
                turn.response_status = str(response.get("status", "unknown"))
                self._record_turn_interval(
                    turn,
                    "volc_last_input_to_response_done",
                    turn.last_input_ns,
                    event_ns,
                    response_status=turn.response_status,
                )
                turn.done.set()
            elif event_type == "error":
                turn.trace.debug("volc_server_error", error=root.get("error"))

    def _handle_audio_frame(self, frame: BridgeFrame) -> None:
        turn = self._current_turn()
        if turn is None:
            return
        with turn.lock:
            turn.audio_bytes += len(frame.payload)
            if turn.first_ai_audio_ns is None:
                turn.first_ai_audio_ns = frame.monotonic_ns
                self._mark_at(turn.trace, "first_ai_audio", frame.monotonic_ns, audio_bytes=len(frame.payload))
                self._record_turn_interval(
                    turn,
                    "volc_last_input_to_first_ai_audio",
                    turn.last_input_ns,
                    frame.monotonic_ns,
                    model=self._session_model,
                )
                self._record_turn_interval(
                    turn,
                    "volc_vad_stop_to_first_ai_audio",
                    turn.server_speech_stopped_ns,
                    frame.monotonic_ns,
                    model=self._session_model,
                )
                self._record_turn_interval(
                    turn,
                    "volc_wakeup_to_first_ai_audio",
                    turn.wake_ns,
                    frame.monotonic_ns,
                    model=self._session_model,
                )
        self._player.enqueue(turn.trace.trace_id, frame.monotonic_ns, frame.payload)

    def _on_first_speaker_write(self, trace_id: str, received_ns: int, write_ns: int) -> None:
        turn = self._current_turn()
        if turn is None or turn.trace.trace_id != trace_id:
            return
        with turn.lock:
            if turn.first_speaker_write_ns is not None:
                return
            turn.first_speaker_write_ns = write_ns
            self._mark_at(turn.trace, "first_speaker_write", write_ns, measurement="PyAudio stream.write start")
            self._record_turn_interval(
                turn,
                "volc_audio_callback_to_speaker_write",
                received_ns,
                write_ns,
            )
            self._record_turn_interval(
                turn,
                "volc_last_input_to_speaker_write",
                turn.last_input_ns,
                write_ns,
                model=self._session_model,
            )
            self._record_turn_interval(
                turn,
                "volc_wakeup_to_speaker_write",
                turn.wake_ns,
                write_ns,
                model=self._session_model,
            )

    def _audio_loop(self) -> None:
        settings = VadSettings(
            sample_rate=int(self.get_parameter("audio_sample_rate").value),
            chunk_ms=int(self.get_parameter("audio_chunk_ms").value),
            pre_roll_ms=int(self.get_parameter("audio_pre_roll_ms").value),
            no_speech_timeout_sec=float(self.get_parameter("audio_no_speech_timeout_sec").value),
            max_utterance_sec=float(self.get_parameter("audio_max_utterance_sec").value),
            min_speech_sec=float(self.get_parameter("audio_min_speech_sec").value),
        )
        input_index = int(self.get_parameter("audio_input_device_index").value)
        recorder = FunVadRecorder(
            settings,
            str(self.get_parameter("funvad_model").value),
            str(self.get_parameter("funvad_device").value),
            input_device_index=input_index if input_index >= 0 else None,
        )
        try:
            self.get_logger().info("Loading FunVAD before accepting wake events.")
            recorder.warm_up()
            self.get_logger().info("FunVAD is ready; faster-whisper and DeepSeek are not used in S2S mode.")
        except Exception as exc:
            self.get_logger().error(f"FunVAD warm-up failed: {exc}")
            return
        if not self._player.wait_ready(10.0):
            self.get_logger().error(self._player.ready_error or "S2S speaker did not become ready")
            return
        if not self._bridge.wait_connected(float(self.get_parameter("bridge_connect_timeout_sec").value)):
            self.get_logger().error("Volcengine S2S bridge did not connect before timeout")
            return

        self._status("ready")
        while not self._stop.is_set():
            turn: TurnState | None = None
            try:
                wake_event = self._wait_for_wake_event()
                if self._stop.is_set():
                    return
                wake_ns = time.monotonic_ns()
                trace = self._debug_sink.start_trace("volc_s2s_audio", wake_event=str(wake_event)[:120])
                self._mark_at(trace, "wakeup_event", wake_ns)
                turn = TurnState(trace=trace, wake_ns=wake_ns)
                self._set_active_turn(turn)
                self._status("wakeup")
                self._play_wakeup_ack(trace)

                self._bridge.clear()
                first_chunk = True

                def stream_chunk(chunk: bytes) -> None:
                    nonlocal first_chunk
                    sent_ns = self._bridge.send_audio(chunk)
                    with turn.lock:
                        if first_chunk:
                            first_chunk = False
                            turn.first_input_ns = sent_ns
                            self._mark_at(trace, "first_input_audio_sent", sent_ns, pcm_bytes=len(chunk))
                            self._record_turn_interval(
                                turn,
                                "volc_wakeup_to_first_input_audio",
                                turn.wake_ns,
                                sent_ns,
                            )
                        turn.last_input_ns = sent_ns

                with trace.phase("local_vad_record"):
                    pcm, reason = recorder.record(chunk_callback=stream_chunk)
                with turn.lock:
                    last_input_ns = turn.last_input_ns
                if last_input_ns is not None:
                    self._mark_at(trace, "last_input_audio_sent", last_input_ns)
                trace.debug(
                    "local_vad_finished",
                    reason=reason,
                    retained_pcm_bytes=len(pcm),
                    streamed_audio=True,
                )

                if reason == "no_speech_timeout" or not pcm:
                    self._bridge.clear()
                    trace.complete(reason if reason else "empty_audio")
                    self._status("ready")
                    continue

                commit_ns = self._bridge.commit()
                with turn.lock:
                    turn.commit_ns = commit_ns
                self._mark_at(trace, "input_commit_sent", commit_ns)
                self._record_turn_interval(
                    turn,
                    "volc_last_input_to_commit",
                    last_input_ns,
                    commit_ns,
                )
                self._status("waiting_response")
                completed = turn.done.wait(float(self.get_parameter("response_timeout_sec").value))
                if completed:
                    playback_wait_started = time.perf_counter()
                    playback_drained = self._player.wait_idle(10.0)
                    trace.record(
                        "speaker_playback_drain",
                        (time.perf_counter() - playback_wait_started) * 1000.0,
                        success=playback_drained,
                    )
                    trace.complete(
                        "s2s_response",
                        model=self._session_model,
                        response_status=turn.response_status,
                        response_audio_bytes=turn.audio_bytes,
                    )
                else:
                    trace.debug("volc_response_timeout")
                    try:
                        self._bridge.interrupt()
                    except Exception:
                        pass
                    trace.complete("response_timeout", model=self._session_model)
                self._status("ready")
                self._stop.wait(float(self.get_parameter("post_response_quiet_sec").value))
            except Exception as exc:
                self.get_logger().error(f"Volcengine S2S audio loop failed: {exc}")
                if turn is not None:
                    turn.trace.debug(
                        "volc_s2s_pipeline_failed",
                        error_type=type(exc).__name__,
                        message=str(exc),
                    )
                    turn.trace.complete("pipeline_error")
                self._status("error")
                self._stop.wait(2.0)
            finally:
                if turn is not None and self._current_turn() is turn:
                    self._set_active_turn(None)

    def _wait_for_wake_event(self) -> str:
        if bool(self.get_parameter("keyboard_wakeup").value):
            prompt = "Press Enter to start one Volcengine S2S turn: "
            try:
                with open("/dev/tty", "r", encoding="utf-8", errors="replace") as terminal:
                    print(prompt, end="", flush=True)
                    terminal.readline()
            except OSError:
                input(prompt)
            return "keyboard"
        try:
            import serial
        except ImportError as exc:
            raise RuntimeError("pyserial is required for iFlytek serial wakeup") from exc
        port = resolve_wakeup_serial_port(
            str(self.get_parameter("wakeup_serial_port").value),
            self.get_logger().warning,
        )
        baud = int(self.get_parameter("wakeup_serial_baud").value)
        detector = SerialWakeDetector(
            str(self.get_parameter("wakeup_match_text").value),
            int(self.get_parameter("wakeup_serial_max_buffer_bytes").value),
        )
        with serial.Serial(port, baud, timeout=0.5) as serial_port:
            while not self._stop.is_set():
                data = serial_port.read(max(1, min(serial_port.in_waiting, 4096)))
                if not data:
                    continue
                if bool(self.get_parameter("wakeup_log_raw").value):
                    self.get_logger().debug(
                        "Wake serial bytes=" + data.decode("utf-8", errors="backslashreplace")
                    )
                matched = detector.feed(data)
                if matched is not None:
                    return matched
        return ""

    def _play_wakeup_ack(self, trace: VoiceTrace) -> None:
        path = Path(str(self.get_parameter("wakeup_ack_cache_file").value)).expanduser()
        started_at = time.perf_counter()
        success = False
        error_type = ""
        try:
            if not path.is_file() or path.stat().st_size <= 128:
                raise FileNotFoundError(path)
            import pygame

            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=24000, size=-16, channels=1, buffer=1024)
            channel = pygame.mixer.Channel(0)
            sound = pygame.mixer.Sound(str(path))
            channel.play(sound)
            deadline = time.monotonic() + float(self.get_parameter("wakeup_ack_playback_timeout_sec").value)
            while channel.get_busy() and not self._stop.is_set() and time.monotonic() < deadline:
                time.sleep(0.01)
            success = not channel.get_busy()
            if not success:
                channel.stop()
        except Exception as exc:
            error_type = type(exc).__name__
            self.get_logger().warning(f"Cached wake acknowledgement playback failed: {exc}")
        trace.record(
            "wakeup_ack_playback",
            (time.perf_counter() - started_at) * 1000.0,
            cached_file=True,
            success=success,
            error_type=error_type or None,
        )

    def _status(self, text: str) -> None:
        self._status_pub.publish(String(data=text))

    def destroy_node(self):
        self._stop.set()
        turn = self._current_turn()
        if turn is not None:
            turn.done.set()
        try:
            self._bridge.interrupt()
        except Exception:
            pass
        self._bridge.close()
        self._player.close()
        if self._audio_thread.is_alive():
            self._audio_thread.join(timeout=2.0)
        try:
            import pygame

            if pygame.mixer.get_init():
                pygame.mixer.quit()
        except Exception:
            pass
        return super().destroy_node()


def main() -> None:
    rclpy.init()
    node = VolcS2SVoiceNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
