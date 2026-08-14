"""Qwen3.5 Omni realtime voice node with local ROS safety execution."""

from __future__ import annotations

import base64
import json
import os
import queue
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import rclpy
from ament_index_python.packages import get_package_share_directory
from nav_msgs.msg import OccupancyGrid, Odometry
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.signals import SignalHandlerOptions
from sensor_msgs.msg import LaserScan
from std_msgs.msg import String

from project_link_voice.wakeup import SerialWakeDetector, resolve_wakeup_serial_port

from .audio import DuplexPcmAudio
from .protocol import RealtimeEvent
from .robot_tools import RobotToolController
from .timing import TimingTrace
from .tools import (
    SYSTEM_PROMPT,
    ToolExecutionResult,
    is_explicit_confirmation,
    is_explicit_exit,
    tool_schemas,
)
from .transport import DashScopeRealtimeTransport


class QwenRealtimeVoiceNode(Node):
    def __init__(self) -> None:
        super().__init__("qwen_realtime_voice_node")
        self._declare_parameters()
        self._stop = threading.Event()
        self._session_ready = threading.Event()
        self._conversation_active = threading.Event()
        self._input_requested = False
        self._microphone_stream_seen = False
        self._event_queue: queue.Queue[RealtimeEvent] = queue.Queue(maxsize=2048)
        self._tool_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="qwen-tools")
        self._trace: TimingTrace | None = None
        self._response_generation = 0
        self._current_response_id = ""
        self._ignored_response_ids: set[str] = set()
        self._playback_failed_response_ids: set[str] = set()
        self._blocked_auto_response = False
        self._blocked_reply: tuple[str, bool] | None = None
        self._response_audio_seen = False
        self._assistant_text_parts: list[str] = []
        self._user_text_parts: dict[str, str] = {}
        self._turns = 0
        self._session_started_at = 0.0
        self._last_activity = time.monotonic()
        self._awaiting_first_speech = False
        self._wake_ack_response = False
        self._wake_ack_capture = bytearray()
        self._end_after_response = False
        self._intentional_session_rotation = False
        self._reconnect_lock = threading.Lock()

        self._status_pub = self.create_publisher(String, "/voice/status", 10)
        self._tts_pub = self.create_publisher(String, "/voice/tts_text", 10)
        self._user_text_pub = self.create_publisher(String, "/voice/user_text", 10)
        self._assistant_text_pub = self.create_publisher(String, "/voice/assistant_text", 10)
        self._event_pub = self.create_publisher(String, "/voice/realtime_event", 20)
        self.create_subscription(String, "/voice/text_input", self._on_text_input, 10)
        self.create_subscription(OccupancyGrid, "/map", self._on_map, 10)
        self.create_subscription(LaserScan, "/scan", self._on_scan, 10)
        self.create_subscription(Odometry, "/odom", self._on_odom, 10)

        self._transport = DashScopeRealtimeTransport(
            callback=self._on_transport_event,
            endpoint=str(self.get_parameter("qwen_realtime_endpoint").value).strip(),
            model=str(self.get_parameter("qwen_realtime_model").value).strip(),
            voice=str(self.get_parameter("qwen_realtime_voice").value).strip(),
            instructions=SYSTEM_PROMPT,
            tools=tool_schemas(bool(self.get_parameter("enable_demo_motion").value)),
            input_sample_rate=int(self.get_parameter("audio_input_sample_rate").value),
            output_sample_rate=int(self.get_parameter("audio_output_sample_rate").value),
            vad_type=str(self.get_parameter("turn_detection_type").value),
            vad_threshold=float(self.get_parameter("turn_detection_threshold").value),
            vad_silence_ms=int(self.get_parameter("turn_detection_silence_duration_ms").value),
            prefix_padding_ms=int(self.get_parameter("prefix_padding_ms").value),
        )
        self._audio: DuplexPcmAudio | None = None
        if bool(self.get_parameter("enable_audio").value):
            self._audio = DuplexPcmAudio(
                input_callback=self._on_microphone_audio,
                input_device_name=str(self.get_parameter("audio_input_device_name").value),
                output_sink=str(self.get_parameter("audio_output_sink").value),
                input_sample_rate=int(self.get_parameter("audio_input_sample_rate").value),
                output_sample_rate=int(self.get_parameter("audio_output_sample_rate").value),
                input_chunk_ms=int(self.get_parameter("audio_input_chunk_ms").value),
                output_chunk_ms=int(self.get_parameter("audio_output_chunk_ms").value),
                queue_seconds=float(self.get_parameter("audio_output_queue_sec").value),
            )
            self._audio.start()
            self.get_logger().info(
                "Audio input ready: "
                f"index={self._audio.input_device_index}, "
                f"name={self._audio.resolved_input_device_name}, "
                f"rate={int(self.get_parameter('audio_input_sample_rate').value)}"
            )

        share_dir = Path(get_package_share_directory("project_link_qwen_realtime_voice"))
        self._robot = RobotToolController(self, share_dir, self._speak_system)

        self._event_thread = threading.Thread(target=self._event_loop, name="qwen-events", daemon=True)
        self._event_thread.start()
        self._connect_thread = threading.Thread(target=self._connect, name="qwen-connect", daemon=True)
        self._connect_thread.start()
        self._wake_thread = None
        if bool(self.get_parameter("enable_audio").value):
            self._wake_thread = threading.Thread(target=self._wake_loop, name="qwen-wakeup", daemon=True)
            self._wake_thread.start()

        self.create_timer(0.2, self._check_conversation_timeout)
        self.create_timer(1.0, self._publish_status)
        self.create_timer(1.0, self._check_task_timeouts)
        self.get_logger().warn(
            "Qwen realtime voice is independent from project_link_voice; never run both voice nodes together."
        )
        self.get_logger().warn(
            "Full-duplex barge-in defaults ON and requires the iFlytek hardware AEC reference wiring."
        )

    def _declare_parameters(self) -> None:
        self.declare_parameter("enable_audio", True)
        self.declare_parameter("enable_motion", False)
        self.declare_parameter("enable_visual_grasp", False)
        self.declare_parameter("enable_demo_motion", False)
        self.declare_parameter("pure_test_mode", "auto")
        self.declare_parameter("navigation_backend", "nav2")
        self.declare_parameter("target_frame", "map")
        self.declare_parameter("base_frame", "base_footprint")
        self.declare_parameter("waypoints_override_file", "")
        self.declare_parameter("nav2_action_name", "/navigate_to_pose")
        self.declare_parameter("nav2_behavior_tree", "")
        self.declare_parameter("nav2_cmd_vel_topic", "/cmd_vel")
        self.declare_parameter("nav2_allowed_cmd_vel_publishers", ["velocity_smoother", "behavior_server"])
        self.declare_parameter("navigation_timeout_sec", 180.0)
        self.declare_parameter("confirmation_timeout_sec", 30.0)
        self.declare_parameter("visual_grasp_timeout_sec", 45.0)
        self.declare_parameter("visual_grasp_prepare_arm", True)
        self.declare_parameter("visual_grasp_action_name", "/visual_grasp/track_and_grasp")
        self.declare_parameter("visual_grasp_connect_service", "/visual_grasp/connect_arm")
        self.declare_parameter("visual_grasp_torque_service", "/visual_grasp/set_torque")
        self.declare_parameter("visual_grasp_stop_service", "/visual_grasp/stop")
        self.declare_parameter(
            "grasp_target_aliases",
            ["药瓶=medicine bottle", "药=medicine bottle", "水杯=red cup", "杯子=cup"],
        )
        self.declare_parameter("demo_cmd_vel_topic", "/cmd_vel")
        self.declare_parameter("demo_linear_mps", 0.08)
        self.declare_parameter("demo_angular_rps", 0.35)
        self.declare_parameter("demo_step_sec", 1.0)
        self.declare_parameter("demo_turn_sec", 1.2)
        self.declare_parameter("demo_spin_sec", 5.5)
        self.declare_parameter(
            "qwen_realtime_endpoint",
            os.environ.get("QWEN_REALTIME_ENDPOINT", ""),
        )
        self.declare_parameter(
            "qwen_realtime_model",
            os.environ.get("QWEN_REALTIME_MODEL", "qwen3.5-omni-flash-realtime"),
        )
        self.declare_parameter("qwen_realtime_voice", os.environ.get("QWEN_REALTIME_VOICE", "Ethan"))
        self.declare_parameter("turn_detection_type", "semantic_vad")
        self.declare_parameter("turn_detection_threshold", 0.5)
        self.declare_parameter("turn_detection_silence_duration_ms", 1200)
        self.declare_parameter("prefix_padding_ms", 300)
        self.declare_parameter("barge_in_enabled", True)
        self.declare_parameter("audio_input_sample_rate", 16000)
        self.declare_parameter("audio_output_sample_rate", 24000)
        self.declare_parameter("audio_input_chunk_ms", 100)
        self.declare_parameter("audio_output_chunk_ms", 50)
        self.declare_parameter("audio_output_queue_sec", 30.0)
        self.declare_parameter(
            "audio_input_device_name",
            os.environ.get("PROJECT_LINK_AUDIO_INPUT_NAME", "XFM-DP-V0.0.18"),
        )
        self.declare_parameter(
            "audio_output_sink",
            os.environ.get(
                "PROJECT_LINK_AUDIO_OUTPUT_DEVICE",
                "alsa_output.usb-C-Media_Electronics_Inc._USB_Audio_Device-00.analog-stereo",
            ),
        )
        self.declare_parameter("wakeup_serial_port", "/dev/project_link_wakeup")
        self.declare_parameter("wakeup_serial_baud", 115200)
        self.declare_parameter("wakeup_match_text", "aiui_event")
        self.declare_parameter("wakeup_serial_max_buffer_bytes", 16384)
        self.declare_parameter("keyboard_wakeup", False)
        self.declare_parameter("wakeup_ack_text", "我在，请说。")
        self.declare_parameter("wakeup_ack_pcm_file", "~/.cache/project_link_qwen_realtime/wakeup_ack.pcm")
        self.declare_parameter("continuous_conversation_enabled", True)
        self.declare_parameter("continuous_silence_timeout_sec", 8.0)
        self.declare_parameter("first_turn_no_speech_timeout_sec", 8.0)
        self.declare_parameter("continuous_max_turns", 20)
        self.declare_parameter("continuous_max_session_sec", 300.0)
        self.declare_parameter("conversation_exit_reply", "好的，我退下了")
        self.declare_parameter("timing_log_file", "~/.ros/project_link_qwen_realtime/voice_timing.jsonl")

    def _connect(self) -> None:
        ready, reason = self._transport.available()
        if not ready:
            self.get_logger().error(f"Qwen realtime unavailable: {reason}")
            return
        try:
            self.get_logger().info(f"Connecting Qwen realtime: {reason}")
            self._transport.connect()
        except Exception as exc:
            self.get_logger().error(f"Qwen realtime connection failed: {exc}")
            self._schedule_reconnect()

    def _schedule_reconnect(self) -> None:
        if self._stop.is_set():
            return

        def reconnect() -> None:
            if not self._reconnect_lock.acquire(blocking=False):
                return
            try:
                delay = 1.0
                while not self._stop.wait(delay):
                    try:
                        self._transport.connect()
                        return
                    except Exception as exc:
                        self.get_logger().error(f"Qwen realtime reconnect failed: {exc}")
                        delay = min(10.0, delay * 2.0)
            finally:
                self._reconnect_lock.release()

        threading.Thread(target=reconnect, name="qwen-reconnect", daemon=True).start()

    def _on_transport_event(self, event: RealtimeEvent) -> None:
        try:
            self._event_queue.put_nowait(event)
        except queue.Full:
            self.get_logger().error("Realtime event queue overflow; reconnecting fail-closed")
            self._session_ready.clear()
            if self._audio is not None:
                self._audio.set_input_enabled(False)
            self._transport.close()
            self._schedule_reconnect()

    def _event_loop(self) -> None:
        while not self._stop.is_set():
            try:
                event = self._event_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self._handle_event(event)
            except Exception as exc:
                self.get_logger().error(f"Realtime event handling failed: {event.type}: {exc}")
            finally:
                self._event_queue.task_done()

    def _handle_event(self, event: RealtimeEvent) -> None:
        event_type = event.type
        payload = event.payload
        self._event_pub.publish(String(data=json.dumps({"type": event_type}, ensure_ascii=False)))
        if event_type == "session.updated":
            self._intentional_session_rotation = False
            self._session_ready.set()
            if self._audio is not None:
                self._audio.set_input_enabled(self._input_requested)
            self.get_logger().info(
                "Qwen realtime session ready: "
                + json.dumps(payload.get("session", {}).get("turn_detection", {}), ensure_ascii=False)
            )
            return
        if event_type == "error":
            self.get_logger().warn(f"Qwen realtime protocol event: {payload}")
            return
        if event_type in ("transport.close", "transport.error"):
            self._session_ready.clear()
            if self._audio is not None:
                self._audio.set_input_enabled(False)
                self._audio.interrupt()
            if self._intentional_session_rotation and event_type == "transport.close":
                self._intentional_session_rotation = False
                self.get_logger().info("Qwen realtime session rotated after conversation")
            else:
                self.get_logger().error(f"Qwen realtime transport event: {event_type}: {payload}")
            self._schedule_reconnect()
            return
        if event_type == "input_audio_buffer.speech_started":
            self._on_speech_started()
            return
        if event_type == "input_audio_buffer.speech_stopped":
            self._last_activity = time.monotonic()
            self._timing("speech_stopped")
            return
        if event_type == "conversation.item.input_audio_transcription.delta":
            self._on_transcript_delta(payload)
            return
        if event_type == "conversation.item.input_audio_transcription.completed":
            self._on_transcript_completed(payload)
            return
        if event_type == "response.created":
            self._on_response_created(payload)
            return
        if event_type == "response.function_call_arguments.done":
            self._on_tool_call(payload)
            return
        if event_type == "response.audio.delta":
            self._on_audio_delta(payload)
            return
        if event_type == "response.audio_transcript.delta":
            text = self._extract_text(payload)
            if text:
                self._assistant_text_parts.append(text)
            return
        if event_type == "response.audio_transcript.done":
            self._on_assistant_text_done(payload)
            return
        if event_type == "response.done":
            self._on_response_done(payload)

    def _on_speech_started(self) -> None:
        self._awaiting_first_speech = False
        self._last_activity = time.monotonic()
        self._timing("speech_started")
        if bool(self.get_parameter("barge_in_enabled").value) and self._response_audio_seen:
            self._transport.cancel_response()
            if self._audio is not None:
                self._audio.interrupt()
            self._timing("barge_in_cancel_sent")

    def _on_transcript_delta(self, payload: dict[str, Any]) -> None:
        item_id = self._item_id(payload)
        delta = self._extract_text(payload)
        if not delta:
            return
        self._user_text_parts[item_id] = self._user_text_parts.get(item_id, "") + delta
        accumulated = self._user_text_parts[item_id]
        if self._robot.active_task is not None and is_explicit_exit(accumulated):
            self._robot.cancel_everything("partial emergency voice keyword")
            self._transport.cancel_response()
            if self._audio is not None:
                self._audio.interrupt()

    def _on_transcript_completed(self, payload: dict[str, Any]) -> None:
        item_id = self._item_id(payload)
        text = self._extract_text(payload) or self._user_text_parts.get(item_id, "")
        self._user_text_parts.pop(item_id, None)
        text = text.strip()
        if not text:
            return
        self._turns += 1
        self._last_activity = time.monotonic()
        self._user_text_pub.publish(String(data=text))
        self.get_logger().info(f"Qwen ASR: {text}")
        self._timing("input_transcription_completed", text_chars=len(text))
        if is_explicit_exit(text):
            self._robot.cancel_everything("explicit exit keyword")
            self._intercept_turn(str(self.get_parameter("conversation_exit_reply").value), True)
            return
        if self._robot.pending_task is not None:
            if is_explicit_confirmation(text):
                reply = self._robot.confirm_pending()
            else:
                reply = "当前有待确认任务。请说确认开始，或说取消。"
            self._intercept_turn(reply, False)

    def _on_response_created(self, payload: dict[str, Any]) -> None:
        response_id = self._response_id(payload)
        self._current_response_id = response_id
        self._assistant_text_parts.clear()
        self._response_audio_seen = False
        if self._audio is not None:
            self._response_generation = self._audio.next_generation()
        if self._blocked_auto_response:
            self._blocked_auto_response = False
            if response_id:
                self._ignored_response_ids.add(response_id)
        self._timing("response_created", response_id=response_id)

    def _on_tool_call(self, payload: dict[str, Any]) -> None:
        call_id = str(payload.get("call_id") or payload.get("id") or "")
        name = str(payload.get("name") or payload.get("function", {}).get("name") or "")
        raw_arguments = payload.get("arguments") or payload.get("function", {}).get("arguments") or "{}"
        try:
            args = json.loads(raw_arguments) if isinstance(raw_arguments, str) else dict(raw_arguments)
        except Exception:
            args = {}
        self._timing("function_call_done", tool=name)
        future = self._tool_executor.submit(self._robot.execute, name, args)
        future.add_done_callback(lambda result: self._on_tool_result(call_id, name, result))

    def _on_tool_result(self, call_id: str, name: str, future) -> None:
        try:
            result: ToolExecutionResult = future.result()
        except Exception as exc:
            result = ToolExecutionResult({"success": False, "message": f"工具执行异常：{exc}"})
        self._timing("python_tool_done", tool=name, success=bool(result.payload.get("success")))
        output = dict(result.payload)
        if result.spoken_reply:
            output["spoken_reply"] = result.spoken_reply
        try:
            self._transport.send_tool_result(call_id, output)
            self._timing("tool_result_sent", tool=name)
            self._transport.create_response()
        except Exception as exc:
            self.get_logger().error(f"Tool result delivery failed: {exc}")

    def _on_audio_delta(self, payload: dict[str, Any]) -> None:
        response_id = self._response_id(payload) or self._current_response_id
        if response_id in self._ignored_response_ids:
            return
        encoded = payload.get("delta") or payload.get("audio") or ""
        if isinstance(encoded, dict):
            encoded = encoded.get("data", "")
        if not isinstance(encoded, str) or not encoded:
            return
        try:
            pcm = base64.b64decode(encoded)
        except Exception:
            return
        if self._wake_ack_response:
            self._wake_ack_capture.extend(pcm)
        if not self._response_audio_seen:
            self._response_audio_seen = True
            self._timing("first_audio_delta", response_id=response_id)
        if self._audio is not None and not self._audio.enqueue(pcm, self._response_generation):
            if response_id not in self._playback_failed_response_ids:
                self._playback_failed_response_ids.add(response_id)
                self.get_logger().error("Realtime playback queue overflow or stale audio; canceling response")
                self._transport.cancel_response()
                self._audio.interrupt()

    def _on_assistant_text_done(self, payload: dict[str, Any]) -> None:
        text = self._extract_text(payload) or "".join(self._assistant_text_parts)
        text = text.strip()
        if not text:
            return
        self._assistant_text_pub.publish(String(data=text))
        self._tts_pub.publish(String(data=text))
        self.get_logger().info(f"Qwen reply: {text}")

    def _on_response_done(self, payload: dict[str, Any]) -> None:
        response_id = self._response_id(payload) or self._current_response_id
        self._timing("response_done", response_id=response_id)
        if response_id in self._playback_failed_response_ids:
            self._playback_failed_response_ids.discard(response_id)
            if self._wake_ack_response:
                self._wake_ack_response = False
                self._wake_ack_capture.clear()
                self._enable_listening(first_turn=True)
            elif self._conversation_active.is_set():
                self._enable_listening(first_turn=False)
            return
        if response_id in self._ignored_response_ids:
            self._ignored_response_ids.discard(response_id)
            blocked = self._blocked_reply
            self._blocked_reply = None
            if blocked is not None:
                self._dispatch_local_reply(*blocked)
            return
        threading.Thread(target=self._after_response_playback, daemon=True).start()

    def _after_response_playback(self) -> None:
        if self._audio is not None:
            self._audio.wait_idle(10.0)
        self._timing("playback_idle")
        if self._wake_ack_response:
            self._wake_ack_response = False
            if self._wake_ack_capture:
                cache_file = Path(str(self.get_parameter("wakeup_ack_pcm_file").value)).expanduser()
                try:
                    cache_file.parent.mkdir(parents=True, exist_ok=True)
                    cache_file.write_bytes(bytes(self._wake_ack_capture))
                    os.chmod(cache_file, 0o600)
                    self.get_logger().info(f"Cached Qwen wake acknowledgement: {cache_file}")
                except OSError as exc:
                    self.get_logger().warn(f"Failed to cache wake acknowledgement: {exc}")
                self._wake_ack_capture.clear()
            self._enable_listening(first_turn=True)
            return
        if self._end_after_response:
            self._end_after_response = False
            self._finish_conversation()
            return
        if not bool(self.get_parameter("continuous_conversation_enabled").value):
            self._finish_conversation()
            return
        if self._conversation_active.is_set():
            self._enable_listening(first_turn=False)

    def _intercept_turn(self, reply: str, end_after: bool) -> None:
        self._transport.cancel_response()
        if self._audio is not None:
            self._audio.interrupt()
        self._blocked_auto_response = True
        self._blocked_reply = (reply, end_after)

        def fallback() -> None:
            time.sleep(0.4)
            if self._blocked_auto_response and self._blocked_reply is not None:
                self._blocked_auto_response = False
                blocked = self._blocked_reply
                self._blocked_reply = None
                self._dispatch_local_reply(*blocked)

        threading.Thread(target=fallback, daemon=True).start()

    def _dispatch_local_reply(self, reply: str, end_after: bool) -> None:
        self._end_after_response = end_after
        self._transport.send_text(f"请严格只朗读以下内容，不要改写：{reply}")

    def _speak_system(self, text: str) -> None:
        if not text or not self._session_ready.is_set():
            return
        try:
            self._transport.send_text(f"请严格只朗读以下内容，不要改写：{text}")
        except Exception as exc:
            self.get_logger().error(f"System speech failed: {exc}")

    def _wake_loop(self) -> None:
        if bool(self.get_parameter("keyboard_wakeup").value):
            while not self._stop.is_set():
                try:
                    input("Press Enter to wake Qwen realtime voice... ")
                except EOFError:
                    return
                self._begin_conversation("keyboard")
            return
        try:
            import serial

            port = resolve_wakeup_serial_port(
                str(self.get_parameter("wakeup_serial_port").value),
                self.get_logger().warn,
            )
            detector = SerialWakeDetector(
                str(self.get_parameter("wakeup_match_text").value),
                int(self.get_parameter("wakeup_serial_max_buffer_bytes").value),
            )
            with serial.Serial(
                port,
                int(self.get_parameter("wakeup_serial_baud").value),
                timeout=0.2,
            ) as stream:
                self.get_logger().info(f"Qwen wake serial ready: {port}")
                while not self._stop.is_set():
                    event = detector.feed(stream.read(256))
                    if event is not None:
                        self._begin_conversation(event)
        except Exception as exc:
            self.get_logger().error(f"Wake serial failed: {exc}")

    def _begin_conversation(self, wake_event: str) -> None:
        if self._conversation_active.is_set():
            return
        if not self._session_ready.wait(timeout=2.0):
            self.get_logger().error("Wake ignored because Qwen session is not ready")
            return
        self._conversation_active.set()
        self._session_started_at = time.monotonic()
        self._turns = 0
        self._trace = TimingTrace(
            str(self.get_parameter("timing_log_file").value),
            self.get_logger(),
        )
        self._timing("wakeup", wake_preview=str(wake_event)[:80])
        self._input_requested = False
        if self._audio is not None:
            self._audio.set_input_enabled(False)
        cache_file = Path(str(self.get_parameter("wakeup_ack_pcm_file").value)).expanduser()
        if self._audio is not None and cache_file.is_file():
            generation = self._audio.next_generation()
            self._audio.enqueue(cache_file.read_bytes(), generation)

            def wait_ack() -> None:
                self._audio.wait_idle(5.0)
                self._enable_listening(first_turn=True)

            threading.Thread(target=wait_ack, daemon=True).start()
            return
        self._wake_ack_response = True
        self._wake_ack_capture.clear()
        self._transport.send_text(
            "请严格只朗读以下内容，不要改写："
            + str(self.get_parameter("wakeup_ack_text").value)
        )

    def _enable_listening(self, first_turn: bool) -> None:
        if not self._conversation_active.is_set():
            return
        self._awaiting_first_speech = first_turn
        self._last_activity = time.monotonic()
        self._response_audio_seen = False
        self._microphone_stream_seen = False
        self._input_requested = True
        if self._audio is not None:
            self._audio.set_input_enabled(self._session_ready.is_set())
        self._timing("listening_started", first_turn=first_turn)

    def _finish_conversation(self) -> None:
        self._input_requested = False
        if self._audio is not None:
            self._audio.set_input_enabled(False)
        self._conversation_active.clear()
        self._trace = None
        self._turns = 0
        self._session_started_at = 0.0
        self.get_logger().info("Qwen realtime conversation ended")
        if not self._stop.is_set():
            self._intentional_session_rotation = True
            self._session_ready.clear()
            self._transport.close()
            self._schedule_reconnect()

    def _check_conversation_timeout(self) -> None:
        if not self._conversation_active.is_set() or self._response_audio_seen:
            return
        now = time.monotonic()
        if self._session_started_at > 0:
            max_session = float(self.get_parameter("continuous_max_session_sec").value)
            if max_session > 0 and now - self._session_started_at >= max_session:
                self._intercept_turn(str(self.get_parameter("conversation_exit_reply").value), True)
                return
        max_turns = int(self.get_parameter("continuous_max_turns").value)
        if max_turns > 0 and self._turns >= max_turns:
            self._intercept_turn(str(self.get_parameter("conversation_exit_reply").value), True)
            return
        timeout = float(
            self.get_parameter(
                "first_turn_no_speech_timeout_sec"
                if self._awaiting_first_speech
                else "continuous_silence_timeout_sec"
            ).value
        )
        if timeout > 0 and now - self._last_activity >= timeout:
            reply = (
                "没有听到有效语音，我先休息了。"
                if self._awaiting_first_speech
                else str(self.get_parameter("conversation_exit_reply").value)
            )
            self._dispatch_local_reply(reply, True)

    def _check_task_timeouts(self) -> None:
        if self._robot.confirmation_expired():
            self._speak_system("任务确认超时，已作废。请重新下达指令。")
        if self._robot.navigation_timed_out():
            self._speak_system("导航超时，当前任务已取消。")

    def _on_microphone_audio(self, pcm: bytes) -> None:
        if self._session_ready.is_set() and self._conversation_active.is_set():
            try:
                if not self._microphone_stream_seen:
                    self._microphone_stream_seen = True
                    peak = max(
                        (abs(sample) for sample, in struct.iter_unpack("<h", pcm)),
                        default=0,
                    )
                    self.get_logger().info(
                        f"Microphone PCM upload started: bytes={len(pcm)}, peak={peak}"
                    )
                    self._timing("microphone_first_chunk", bytes=len(pcm), peak=peak)
                self._transport.append_audio(pcm)
            except Exception as exc:
                self.get_logger().error(f"Microphone upload failed: {exc}")

    def _on_text_input(self, message: String) -> None:
        text = message.data.strip()
        if not text or not self._session_ready.is_set():
            return
        if not self._conversation_active.is_set():
            self._conversation_active.set()
            self._session_started_at = time.monotonic()
            self._trace = TimingTrace(str(self.get_parameter("timing_log_file").value), self.get_logger())
        self._last_activity = time.monotonic()
        self._awaiting_first_speech = False
        if is_explicit_exit(text):
            self._robot.cancel_everything("text input exit")
            self._dispatch_local_reply(str(self.get_parameter("conversation_exit_reply").value), True)
        elif self._robot.pending_task is not None:
            reply = self._robot.confirm_pending() if is_explicit_confirmation(text) else "请说确认开始，或说取消。"
            self._dispatch_local_reply(reply, False)
        else:
            self._transport.send_text(text)

    def _on_map(self, _message: OccupancyGrid) -> None:
        self._robot.map_seen = True

    def _on_scan(self, _message: LaserScan) -> None:
        self._robot.scan_seen = True

    def _on_odom(self, _message: Odometry) -> None:
        self._robot.odom_seen = True

    def _publish_status(self) -> None:
        status = {
            "backend": "qwen_realtime",
            "session_ready": self._session_ready.is_set(),
            "conversation_active": self._conversation_active.is_set(),
            "barge_in": bool(self.get_parameter("barge_in_enabled").value),
            "pending_task": self._robot.pending_task.kind if self._robot.pending_task else "",
            "active_task": self._robot.active_task.kind if self._robot.active_task else "",
            "sdk_version": self._transport.sdk_version,
        }
        self._status_pub.publish(String(data=json.dumps(status, ensure_ascii=False)))

    def _timing(self, phase: str, **fields: Any) -> None:
        if self._trace is not None:
            self._trace.event(phase, **fields)

    @staticmethod
    def _extract_text(payload: dict[str, Any]) -> str:
        for key in ("transcript", "text", "delta"):
            value = payload.get(key)
            if isinstance(value, str):
                return value
        item = payload.get("item")
        if isinstance(item, dict):
            for key in ("transcript", "text"):
                value = item.get(key)
                if isinstance(value, str):
                    return value
        return ""

    @staticmethod
    def _item_id(payload: dict[str, Any]) -> str:
        return str(payload.get("item_id") or payload.get("id") or "default")

    @staticmethod
    def _response_id(payload: dict[str, Any]) -> str:
        response = payload.get("response")
        if isinstance(response, dict):
            return str(response.get("id") or "")
        return str(payload.get("response_id") or payload.get("id") or "")

    def destroy_node(self):
        self._stop.set()
        self._input_requested = False
        self._robot.shutdown()
        cleanup_steps = []
        if self._audio is not None:
            cleanup_steps.append(self._audio.close)
        cleanup_steps.extend([
            self._transport.close,
            lambda: self._tool_executor.shutdown(wait=False, cancel_futures=True),
        ])
        if self._event_thread.is_alive():
            cleanup_steps.append(lambda: self._event_thread.join(timeout=1.0))
        for cleanup in cleanup_steps:
            try:
                cleanup()
            except KeyboardInterrupt:
                continue
        return super().destroy_node()


def main() -> None:
    rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
    node = QwenRealtimeVoiceNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        try:
            node.destroy_node()
        except KeyboardInterrupt:
            pass
        try:
            if rclpy.ok():
                rclpy.shutdown()
        except (KeyboardInterrupt, ExternalShutdownException):
            pass
