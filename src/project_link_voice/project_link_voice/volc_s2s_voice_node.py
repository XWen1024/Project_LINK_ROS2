#!/usr/bin/env python3
"""Local wake/microphone/speaker integration for Volcengine WebSocket S2S."""

from __future__ import annotations

import json
import os
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from .voice_debug import VoiceDebugSink, VoiceTrace
from .volc_s2s_bridge import (
    EVT_AUDIO,
    EVT_CONTROL,
    EVT_MESSAGE,
    BridgeFrame,
    VolcS2SBridgeProcess,
)
from .volc_s2s_tools import (
    FunctionCall,
    build_followup_response_event,
    build_function_output_event,
    execute_safe_function,
    function_call_from_arguments_done,
    function_call_from_item,
    function_calls_from_legacy_array,
)
from .volc_s2s_microphone import RawPcmCaptureSettings, ServerVadPcmRecorder
from .volc_s2s_session import (
    can_continue_session,
    endpoint_event_belongs_to_turn,
    input_event_belongs_to_turn,
    no_speech_outcome,
    response_audio_belongs_to_turn,
    response_event_belongs_to_turn,
)
from .wakeup import SerialWakeDetector, resolve_wakeup_serial_port


@dataclass
class TurnState:
    trace: VoiceTrace
    wake_ns: int
    session_id: str = ""
    turn_index: int = 1
    continuation: bool = False
    done: threading.Event = field(default_factory=threading.Event)
    server_input_done: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    first_input_ns: int | None = None
    last_input_ns: int | None = None
    commit_ns: int | None = None
    server_speech_started_ns: int | None = None
    server_speech_stopped_ns: int | None = None
    server_commit_ns: int | None = None
    function_call_ns: int | None = None
    function_args_done_ns: int | None = None
    response_created_ns: int | None = None
    first_ai_audio_ns: int | None = None
    first_speaker_write_ns: int | None = None
    response_audio_done_ns: int | None = None
    response_done_ns: int | None = None
    function_output_sent_ns: int | None = None
    pending_call_id: str = ""
    pending_function_name: str = ""
    pending_arguments: str = "{}"
    function_output_sent: bool = False
    final_response_created_ns: int | None = None
    first_ai_audio_after_function_ns: int | None = None
    audio_bytes: int = 0
    response_status: str = ""
    unexpected_audio_dropped_bytes: int = 0


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
        self._session_ready = threading.Event()
        self._startup_trace = self._debug_sink.start_trace("volc_s2s_startup")
        self._bridge_lock = threading.Lock()

        output_index = int(self.get_parameter("audio_output_device_index").value)
        self._player = PcmPlaybackWorker(
            sample_rate=int(self.get_parameter("audio_sample_rate").value),
            output_device_index=output_index if output_index >= 0 else None,
            pulse_sink=str(self.get_parameter("pulse_sink").value),
            first_write_callback=self._on_first_speaker_write,
            error_callback=self.get_logger().error,
        )

        self._bridge_executable = str(self.get_parameter("native_bridge_executable").value)
        self._bridge = VolcS2SBridgeProcess(
            self._bridge_executable,
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
        self.declare_parameter("continuous_conversation_enabled", True)
        self.declare_parameter("continuous_max_turns", 8)
        self.declare_parameter("keyboard_wakeup", False)
        self.declare_parameter("wakeup_serial_port", "auto")
        self.declare_parameter("wakeup_serial_baud", 115200)
        self.declare_parameter("wakeup_match_text", "aiui_event")
        self.declare_parameter("wakeup_serial_max_buffer_bytes", 16384)
        self.declare_parameter("wakeup_log_raw", False)
        self.declare_parameter("wakeup_ack_cache_file", "~/.cache/project_link_voice/wakeup_ack.mp3")
        self.declare_parameter("wakeup_ack_playback_timeout_sec", 5.0)
        self.declare_parameter("audio_sample_rate", 16000)
        self.declare_parameter("audio_chunk_ms", 100)
        self.declare_parameter("audio_no_speech_timeout_sec", 8.0)
        self.declare_parameter("audio_max_utterance_sec", 30.0)
        self.declare_parameter("audio_input_device_index", 0)
        self.declare_parameter("audio_input_device_name", "XFM-DP-V0.0.18")
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

    def _wait_session_ready(self, timeout_sec: float | None = None) -> bool:
        timeout = (
            float(self.get_parameter("bridge_connect_timeout_sec").value)
            if timeout_sec is None
            else max(0.0, float(timeout_sec))
        )
        return self._session_ready.wait(timeout)

    def _restart_bridge(self, force: bool = False) -> bool:
        with self._bridge_lock:
            if self._bridge.connected and self._session_ready.is_set() and not force:
                return True
            started_ns = time.monotonic_ns()
            self.get_logger().warning("Volcengine WSS is disconnected; recreating the native bridge.")
            self._status("reconnecting")
            self._session_ready.clear()
            self._bridge.close()
            replacement = VolcS2SBridgeProcess(
                self._bridge_executable,
                self._on_bridge_frame,
                self.get_logger().error,
            )
            replacement.start()
            self._bridge = replacement
            connected = replacement.wait_connected(
                float(self.get_parameter("bridge_connect_timeout_sec").value)
            )
            session_ready = connected and self._wait_session_ready()
            elapsed_ms = (time.monotonic_ns() - started_ns) / 1_000_000.0
            self.get_logger().info(
                f"Volcengine WSS reconnect success={connected} "
                f"session_ready={session_ready} elapsed_ms={elapsed_ms:.3f}"
            )
            self._status("connected" if session_ready else "disconnected")
            return session_ready

    def _begin_reconnect_if_needed(self) -> threading.Thread | None:
        if self._bridge.connected:
            return None
        thread = threading.Thread(target=self._restart_bridge, name="volc-s2s-reconnect", daemon=True)
        thread.start()
        return thread

    def _remember_function_call_locked(
        self,
        turn: TurnState,
        call: FunctionCall,
        event_ns: int,
        event_type: str,
    ) -> None:
        turn.pending_call_id = call.call_id
        turn.pending_function_name = call.name
        turn.pending_arguments = call.arguments
        if turn.function_call_ns is None:
            turn.function_call_ns = event_ns
            self._record_turn_interval(
                turn,
                "volc_vad_stop_to_function_call",
                turn.server_speech_stopped_ns,
                event_ns,
                function=call.name,
            )
            self._record_turn_interval(
                turn,
                "volc_last_input_to_function_call",
                turn.last_input_ns,
                event_ns,
                function=call.name,
            )
        turn.trace.debug(
            "volc_function_call_received",
            event_type=event_type,
            function=call.name,
            call_id=call.call_id,
            arguments=call.arguments[:2048],
        )
        if turn.function_call_ns == event_ns:
            self.get_logger().info(
                f"[TOOL] received function={call.name} call_id={call.call_id} "
                f"session={turn.session_id} turn={turn.turn_index}"
            )

    def _return_function_output(self, turn: TurnState, call: FunctionCall) -> None:
        with turn.lock:
            if turn.function_output_sent or self._current_turn() is not turn:
                return
            turn.function_output_sent = True
        execute_started_ns = time.monotonic_ns()
        output = execute_safe_function(call)
        execute_done_ns = time.monotonic_ns()
        turn.trace.record(
            "volc_local_function_execute",
            (execute_done_ns - execute_started_ns) / 1_000_000.0,
            function=call.name,
            success="error" not in output,
        )
        try:
            output_sent_ns, output_send_result = self._bridge.send_json_checked(
                build_function_output_event(call.call_id, output)
            )
            if output_send_result != 0:
                raise RuntimeError(
                    f"function_call_output send failed result={output_send_result}"
                )
            with turn.lock:
                turn.function_output_sent_ns = output_sent_ns
            response_create_ns, response_create_result = self._bridge.send_json_checked(
                build_followup_response_event()
            )
            if response_create_result != 0:
                raise RuntimeError(
                    f"function follow-up response.create failed result={response_create_result}"
                )
        except Exception:
            with turn.lock:
                turn.function_output_sent = False
                turn.function_output_sent_ns = None
            raise
        self._record_turn_interval(
            turn,
            "volc_function_call_to_output_sent",
            turn.function_call_ns,
            output_sent_ns,
            function=call.name,
        )
        turn.trace.record(
            "volc_function_output_to_response_create_sent",
            (response_create_ns - output_sent_ns) / 1_000_000.0,
            function=call.name,
        )
        turn.trace.debug(
            "volc_function_output_returned",
            function=call.name,
            call_id=call.call_id,
            arguments=call.arguments[:2048],
            result=output,
        )
        self.get_logger().info(
            f"[TOOL] output sent function={call.name} call_id={call.call_id} "
            f"result={json.dumps(output, ensure_ascii=False, separators=(',', ':'))}"
        )

    def _schedule_function_output(self, turn: TurnState, call: FunctionCall) -> None:
        """Return tool output outside the native bridge reader thread.

        send_json_checked() waits for a command_result frame.  That frame is
        consumed by the bridge reader thread, so waiting from the reader
        callback itself deadlocks until the timeout expires.
        """

        def worker() -> None:
            try:
                self._return_function_output(turn, call)
            except Exception as exc:
                turn.trace.debug(
                    "volc_function_output_failed",
                    function=call.name,
                    call_id=call.call_id,
                    error=str(exc),
                )
                self.get_logger().error(
                    f"Failed to return Volcengine function output "
                    f"function={call.name}: {exc}"
                )

        threading.Thread(
            target=worker,
            name="volc-s2s-function-output",
            daemon=True,
        ).start()

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
                self._session_ready.clear()
                self._status("disconnected")
        elif event == "conversation_status":
            turn = self._current_turn()
            if turn is not None:
                status_name = str(value.get("name", "UNKNOWN"))
                turn.trace.debug(
                    "volc_conversation_status",
                    status=value.get("status"),
                    name=status_name,
                )
                event_ns = frame.monotonic_ns
                with turn.lock:
                    input_armed = input_event_belongs_to_turn(turn.first_input_ns, event_ns)
                if status_name == "LISTENING" and input_armed:
                    with turn.lock:
                        if turn.server_speech_started_ns is None:
                            turn.server_speech_started_ns = event_ns
                            self._record_turn_interval(
                                turn,
                                "volc_first_input_to_speech_started",
                                turn.first_input_ns,
                                event_ns,
                            )
                            self.get_logger().info(
                                f"[TURN] speech started session={turn.session_id} "
                                f"turn={turn.turn_index}"
                            )
                elif status_name == "THINKING" and endpoint_event_belongs_to_turn(
                    turn.first_input_ns,
                    turn.server_speech_started_ns,
                    event_ns,
                ):
                    with turn.lock:
                        if turn.server_speech_stopped_ns is None:
                            turn.server_speech_stopped_ns = event_ns
                            self._record_turn_interval(
                                turn,
                                "volc_last_input_to_speech_stopped",
                                turn.last_input_ns,
                                event_ns,
                            )
                            self.get_logger().info(
                                f"[TURN] speech stopped session={turn.session_id} "
                                f"turn={turn.turn_index}"
                            )
                    turn.server_input_done.set()
                if status_name == "ANSWER_FINISH":
                    with turn.lock:
                        function_response_ready = (
                            (
                                turn.function_call_ns is None
                                and (
                                    turn.response_created_ns is not None
                                    or turn.first_ai_audio_ns is not None
                                )
                            )
                            or (
                                turn.function_call_ns is not None
                                and (
                                    turn.final_response_created_ns is not None
                                    or turn.first_ai_audio_after_function_ns is not None
                                )
                            )
                        )
                        if turn.response_done_ns is None and function_response_ready:
                            turn.response_done_ns = event_ns
                            turn.response_status = "completed"
                            self._record_turn_interval(
                                turn,
                                "volc_last_input_to_response_done",
                                turn.last_input_ns,
                                event_ns,
                                response_status=turn.response_status,
                                completion_signal="VOLC_CONV_STATUS_ANSWER_FINISH",
                            )
                            turn.done.set()
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
            self._session_ready.set()
            self.get_logger().info(f"Volcengine S2S session model: {self._session_model}")
            self.get_logger().info(
                f"[SESSION] ready model={self._session_model} "
                f"turn_detection={session.get('turn_detection')}"
            )
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
            input_armed = input_event_belongs_to_turn(turn.first_input_ns, event_ns)
            if (
                event_type == "input_audio_buffer.speech_started"
                and input_armed
                and turn.server_speech_started_ns is None
            ):
                turn.server_speech_started_ns = event_ns
                self._record_turn_interval(
                    turn,
                    "volc_first_input_to_speech_started",
                    turn.first_input_ns,
                    event_ns,
                )
                self.get_logger().info(
                    f"[TURN] speech started session={turn.session_id} turn={turn.turn_index}"
                )
            elif (
                event_type == "input_audio_buffer.speech_stopped"
                and endpoint_event_belongs_to_turn(
                    turn.first_input_ns,
                    turn.server_speech_started_ns,
                    event_ns,
                )
                and turn.server_speech_stopped_ns is None
            ):
                turn.server_speech_stopped_ns = event_ns
                self._record_turn_interval(
                    turn,
                    "volc_last_input_to_speech_stopped",
                    turn.last_input_ns,
                    event_ns,
                )
                self.get_logger().info(
                    f"[TURN] speech stopped session={turn.session_id} turn={turn.turn_index}"
                )
                turn.server_input_done.set()
            elif event_type == "input_audio_buffer.committed" and endpoint_event_belongs_to_turn(
                turn.first_input_ns,
                turn.server_speech_started_ns,
                event_ns,
            ):
                turn.server_input_done.set()
                if turn.server_commit_ns is None:
                    turn.server_commit_ns = event_ns
                    self._record_turn_interval(
                        turn,
                        "volc_last_input_to_server_commit",
                        turn.last_input_ns,
                        event_ns,
                        commit_owner="server_vad" if turn.commit_ns is None else "local_safety_guard",
                    )
                    self._record_turn_interval(
                        turn,
                        "volc_commit_to_server_ack",
                        turn.commit_ns,
                        event_ns,
                    )
            elif (
                event_type == "response.created"
                and response_event_belongs_to_turn(
                    turn.first_input_ns,
                    turn.server_input_done.is_set(),
                    event_ns,
                )
                and turn.response_created_ns is None
            ):
                turn.response_created_ns = event_ns
                if turn.function_output_sent_ns is not None and event_ns >= turn.function_output_sent_ns:
                    turn.final_response_created_ns = event_ns
                    self._record_turn_interval(
                        turn,
                        "volc_function_output_to_response_created",
                        turn.function_output_sent_ns,
                        event_ns,
                    )
                self._record_turn_interval(
                    turn,
                    "volc_last_input_to_response_created",
                    turn.last_input_ns,
                    event_ns,
                )
            elif event_type == "response.created":
                turn.trace.debug(
                    "volc_response_created_ignored",
                    reason="input_not_ended",
                    first_input_ns=turn.first_input_ns,
                    server_input_done=turn.server_input_done.is_set(),
                )
            elif event_type in {"conversation.item.created", "response.output_item.done"}:
                item = root.get("item") if isinstance(root.get("item"), dict) else {}
                call = function_call_from_item(item)
                if call is not None:
                    self._remember_function_call_locked(turn, call, event_ns, event_type)
            elif event_type == "response.function_call_arguments.done":
                turn.function_args_done_ns = event_ns
                fallback = None
                if turn.pending_call_id and turn.pending_function_name:
                    fallback = FunctionCall(
                        turn.pending_call_id,
                        turn.pending_function_name,
                        turn.pending_arguments,
                    )
                ready_call = function_call_from_arguments_done(root, fallback)
                if ready_call is not None:
                    self._remember_function_call_locked(turn, ready_call, event_ns, event_type)
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
                response = root.get("response") if isinstance(root.get("response"), dict) else {}
                function_response_ready = (
                    (
                        turn.function_call_ns is None
                        and (
                            turn.response_created_ns is not None
                            or turn.first_ai_audio_ns is not None
                        )
                    )
                    or (
                        turn.function_call_ns is not None
                        and (
                            turn.final_response_created_ns is not None
                            or turn.first_ai_audio_after_function_ns is not None
                        )
                    )
                )
                if turn.response_done_ns is None and function_response_ready:
                    turn.response_done_ns = event_ns
                    turn.response_status = str(response.get("status", "unknown"))
                    self._record_turn_interval(
                        turn,
                        "volc_last_input_to_response_done",
                        turn.last_input_ns,
                        event_ns,
                        response_status=turn.response_status,
                        completion_signal="response.done",
                    )
                    turn.done.set()
            elif event_type == "error":
                turn.trace.debug("volc_server_error", error=root.get("error"))
            else:
                ready_call = None

        if event_type == "response.function_call_arguments.done" and ready_call is not None:
            self._schedule_function_output(turn, ready_call)
        for legacy_call in function_calls_from_legacy_array(root):
            with turn.lock:
                self._remember_function_call_locked(turn, legacy_call, event_ns, "tool_calls")
            self._schedule_function_output(turn, legacy_call)

    def _handle_audio_frame(self, frame: BridgeFrame) -> None:
        turn = self._current_turn()
        if turn is None:
            return
        with turn.lock:
            # A newly-created/reconnected low-load session can emit a short
            # audio frame before the user's response exists. Never let that
            # frame stop microphone capture, pollute latency, or reach the
            # speaker. Official response ordering exposes response.created
            # before response audio for a real user turn.
            if not response_audio_belongs_to_turn(
                turn.first_input_ns,
                turn.response_created_ns,
                frame.monotonic_ns,
            ):
                turn.unexpected_audio_dropped_bytes += len(frame.payload)
                if turn.unexpected_audio_dropped_bytes == len(frame.payload):
                    turn.trace.debug(
                        "volc_unexpected_audio_dropped",
                        audio_bytes=len(frame.payload),
                        reason="response_not_created",
                    )
                    self.get_logger().warning(
                        f"[RESPONSE] dropped pre-response audio session={turn.session_id} "
                        f"turn={turn.turn_index} bytes={len(frame.payload)}"
                    )
                return
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
                self.get_logger().info(
                    f"[RESPONSE] first audio received session={turn.session_id} "
                    f"turn={turn.turn_index} bytes={len(frame.payload)}"
                )
            if (
                turn.function_output_sent_ns is not None
                and frame.monotonic_ns >= turn.function_output_sent_ns
                and turn.first_ai_audio_after_function_ns is None
            ):
                turn.first_ai_audio_after_function_ns = frame.monotonic_ns
                self._record_turn_interval(
                    turn,
                    "volc_function_output_to_first_ai_audio",
                    turn.function_output_sent_ns,
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
            self.get_logger().info(
                f"[RESPONSE] playback started session={turn.session_id} turn={turn.turn_index}"
            )
            self._record_turn_interval(
                turn,
                "volc_wakeup_to_speaker_write",
                turn.wake_ns,
                write_ns,
                model=self._session_model,
            )
            self._record_turn_interval(
                turn,
                "volc_function_output_to_speaker_write",
                turn.function_output_sent_ns,
                write_ns,
                model=self._session_model,
            )

    def _audio_loop(self) -> None:
        settings = RawPcmCaptureSettings(
            sample_rate=int(self.get_parameter("audio_sample_rate").value),
            chunk_ms=int(self.get_parameter("audio_chunk_ms").value),
            no_speech_timeout_sec=float(self.get_parameter("audio_no_speech_timeout_sec").value),
            max_utterance_sec=float(self.get_parameter("audio_max_utterance_sec").value),
        )
        input_index = int(self.get_parameter("audio_input_device_index").value)
        recorder = ServerVadPcmRecorder(
            settings,
            input_device_index=input_index if input_index >= 0 else None,
            input_device_name=str(self.get_parameter("audio_input_device_name").value),
            device_selected_callback=lambda index, name: self.get_logger().info(
                f"Selected microphone index={index if index is not None else 'default'} name={name}"
            ),
        )
        self.get_logger().info(
            "Cloud server_vad owns speech endpointing; local FunVAD, faster-whisper, and DeepSeek are not used."
        )
        if not self._player.wait_ready(10.0):
            self.get_logger().error(self._player.ready_error or "S2S speaker did not become ready")
            return
        if not self._bridge.wait_connected(float(self.get_parameter("bridge_connect_timeout_sec").value)):
            self.get_logger().error("Volcengine S2S bridge did not connect before timeout")
            return
        if not self._wait_session_ready():
            self.get_logger().error("Volcengine session.created was not received before timeout")
            return

        self._status("ready")
        while not self._stop.is_set():
            turn: TurnState | None = None
            try:
                wake_event = self._wait_for_wake_event()
                if self._stop.is_set():
                    return
                session_id = uuid.uuid4().hex[:10]
                session_wake_ns = time.monotonic_ns()
                first_trace = self._debug_sink.start_trace(
                    "volc_s2s_audio",
                    wake_event=str(wake_event)[:120],
                    session_id=session_id,
                    turn_index=1,
                    continuation=False,
                )
                self._mark_at(first_trace, "wakeup_event", session_wake_ns)
                self._status("wakeup")
                self.get_logger().info(
                    f"[SESSION] activated session={session_id} continuous="
                    f"{bool(self.get_parameter('continuous_conversation_enabled').value)}"
                )
                preflight_started_ns = time.monotonic_ns()
                preflight_sent_ns, preflight_result = self._bridge.clear_checked()
                first_trace.record(
                    "volc_session_preflight",
                    (time.monotonic_ns() - preflight_started_ns) / 1_000_000.0,
                    result=preflight_result,
                )
                if preflight_result != 0 or not self._session_ready.is_set():
                    if not self._restart_bridge(force=True):
                        raise RuntimeError("Volcengine session preflight reconnect failed")
                    preflight_sent_ns, preflight_result = self._bridge.clear_checked()
                    if preflight_result != 0:
                        raise RuntimeError(
                            f"Volcengine session preflight clear failed result={preflight_result}"
                        )
                first_trace.debug(
                    "volc_session_preflight_ready",
                    client_timestamp_ns=preflight_sent_ns,
                    session_ready=self._session_ready.is_set(),
                )
                wakeup_ack_done_ns = self._play_wakeup_ack(first_trace)
                continuous_enabled = bool(
                    self.get_parameter("continuous_conversation_enabled").value
                )
                max_turns = max(1, int(self.get_parameter("continuous_max_turns").value))
                session_exit_reason = "single_turn_complete"

                for turn_index in range(1, max_turns + 1):
                    continuation = turn_index > 1
                    turn_start_ns = session_wake_ns if not continuation else time.monotonic_ns()
                    trace = (
                        first_trace
                        if not continuation
                        else self._debug_sink.start_trace(
                            "volc_s2s_audio",
                            session_id=session_id,
                            turn_index=turn_index,
                            continuation=True,
                        )
                    )
                    if continuation:
                        self._mark_at(trace, "continuous_listen_started", turn_start_ns)
                    turn = TurnState(
                        trace=trace,
                        wake_ns=turn_start_ns,
                        session_id=session_id,
                        turn_index=turn_index,
                        continuation=continuation,
                    )
                    self._set_active_turn(turn)
                    self._status("continuous_listening" if continuation else "listening")
                    self.get_logger().info(
                        f"[TURN] listening session={session_id} turn={turn_index} "
                        f"continuation={continuation}"
                    )

                    try:
                        if not self._session_ready.is_set():
                            if continuation:
                                trace.complete("continuous_connection_lost", model=self._session_model)
                                session_exit_reason = "connection_lost"
                                break
                            if not self._restart_bridge(force=True):
                                raise RuntimeError(
                                    "Volcengine session was not ready before microphone capture"
                                )

                        probe_started_ns = time.monotonic_ns()
                        clear_sent_ns, clear_result = self._bridge.clear_checked()
                        trace.record(
                            "volc_pre_turn_connection_probe",
                            (time.monotonic_ns() - probe_started_ns) / 1_000_000.0,
                            result=clear_result,
                        )
                        if clear_result != 0:
                            if continuation:
                                trace.complete(
                                    "continuous_connection_lost",
                                    model=self._session_model,
                                    clear_result=clear_result,
                                )
                                session_exit_reason = "connection_lost"
                                break
                            if not self._restart_bridge(force=True):
                                raise RuntimeError(
                                    "Volcengine WSS reconnect failed before microphone capture"
                                )
                            clear_sent_ns, clear_result = self._bridge.clear_checked()
                            if clear_result != 0:
                                raise RuntimeError(
                                    f"Volcengine input clear failed after reconnect result={clear_result}"
                                )
                        if not self._session_ready.is_set():
                            raise RuntimeError("Volcengine session.created missing after clear")
                        trace.debug(
                            "volc_input_buffer_cleared",
                            client_timestamp_ns=clear_sent_ns,
                        )
                        first_chunk = True

                        def stream_chunk(chunk: bytes) -> None:
                            nonlocal first_chunk
                            sent_ns = self._bridge.send_audio(chunk)
                            with turn.lock:
                                if first_chunk:
                                    first_chunk = False
                                    turn.first_input_ns = sent_ns
                                    self._mark_at(
                                        trace,
                                        "first_input_audio_sent",
                                        sent_ns,
                                        pcm_bytes=len(chunk),
                                    )
                                    self._record_turn_interval(
                                        turn,
                                        "volc_wakeup_to_first_input_audio",
                                        turn.wake_ns,
                                        sent_ns,
                                        continuation=continuation,
                                    )
                                    if continuation:
                                        self._record_turn_interval(
                                            turn,
                                            "continuous_listen_to_first_input_audio",
                                            turn_start_ns,
                                            sent_ns,
                                        )
                                    else:
                                        self._record_turn_interval(
                                            turn,
                                            "wakeup_ack_to_first_input_audio",
                                            wakeup_ack_done_ns,
                                            sent_ns,
                                        )
                                turn.last_input_ns = sent_ns

                        def speech_started_ns() -> int | None:
                            with turn.lock:
                                return turn.server_speech_started_ns

                        with trace.phase("raw_pcm_capture"):
                            pcm_bytes, reason = recorder.record(
                                turn.server_input_done,
                                speech_started_ns,
                                stream_chunk,
                            )
                        with turn.lock:
                            last_input_ns = turn.last_input_ns
                        if last_input_ns is not None:
                            self._mark_at(trace, "last_input_audio_sent", last_input_ns)
                        trace.debug(
                            "cloud_vad_capture_finished",
                            reason=reason,
                            pcm_bytes_sent=pcm_bytes,
                            streamed_audio=True,
                        )

                        if reason == "no_speech_timeout" or pcm_bytes == 0:
                            try:
                                self._bridge.clear()
                            except Exception:
                                pass
                            outcome = no_speech_outcome(continuation)
                            trace.complete(outcome, model=self._session_model)
                            session_exit_reason = outcome
                            break

                        if reason == "max_utterance_timeout":
                            commit_ns, commit_result = self._bridge.commit_checked()
                            if commit_result != 0:
                                raise RuntimeError(
                                    f"Volcengine hard-timeout commit failed result={commit_result}"
                                )
                            with turn.lock:
                                turn.commit_ns = commit_ns
                            self._mark_at(trace, "input_commit_sent", commit_ns, hard_timeout=True)
                            self._record_turn_interval(
                                turn,
                                "volc_last_input_to_commit",
                                last_input_ns,
                                commit_ns,
                                hard_timeout=True,
                            )
                        self._status("waiting_response")
                        completed = turn.done.wait(
                            float(self.get_parameter("response_timeout_sec").value)
                        )
                        if not completed:
                            trace.debug("volc_response_timeout")
                            try:
                                self._bridge.interrupt()
                            except Exception:
                                pass
                            trace.complete("response_timeout", model=self._session_model)
                            session_exit_reason = "response_timeout"
                            break

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
                            session_id=session_id,
                            turn_index=turn_index,
                        )
                        self.get_logger().info(
                            f"[RESPONSE] playback completed session={session_id} "
                            f"turn={turn_index} bytes={turn.audio_bytes}"
                        )
                    finally:
                        if self._current_turn() is turn:
                            self._set_active_turn(None)

                    should_continue, session_exit_reason = can_continue_session(
                        continuous_enabled,
                        turn_index,
                        max_turns,
                        self._bridge.connected,
                        self._session_ready.is_set(),
                    )
                    if not should_continue:
                        break
                    self._stop.wait(float(self.get_parameter("post_response_quiet_sec").value))
                    if self._stop.is_set():
                        return
                    self.get_logger().info(
                        f"[SESSION] awaiting continuation session={session_id} "
                        f"next_turn={turn_index + 1}"
                    )

                self.get_logger().info(
                    f"[SESSION] exited session={session_id} reason={session_exit_reason}"
                )
                self._status("ready")
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

    def _play_wakeup_ack(self, trace: VoiceTrace) -> int:
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
        completed_ns = time.monotonic_ns()
        trace.record(
            "wakeup_ack_playback",
            (time.perf_counter() - started_at) * 1000.0,
            cached_file=True,
            success=success,
            error_type=error_type or None,
        )
        return completed_ns

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
