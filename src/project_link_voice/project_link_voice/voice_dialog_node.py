#!/usr/bin/env python3
"""ROS 2 voice orchestrator: ASR -> LLM tools -> confirmation -> guarded actions."""

from __future__ import annotations

import math
import os
import queue
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import rclpy
from ament_index_python.packages import get_package_share_directory
from geometry_msgs.msg import PoseStamped, Twist
from nav_msgs.msg import OccupancyGrid, Odometry
from rclpy.action import ActionClient
from rclpy.node import Node
from sensor_msgs.msg import LaserScan
from std_msgs.msg import String
from std_srvs.srv import SetBool, Trigger
from tf2_ros import Buffer, TransformException, TransformListener
from wheeltec_robot_msg.action import TrackAndGrasp

from project_link_voice_interfaces.action import DriveToPoint

from .funvad import FunVadRecorder, VadSettings
from .llm import ToolCallingClient, ToolResult
from .task_parser import parse_aliases
from .volcano_tts import VolcanoTts
from .waypoints import CANCEL_WORDS, CONFIRM_WORDS, Waypoint, WaypointStore, contains_any


class WhisperTranscriber:
    """Lazy faster-whisper wrapper so missing audio dependencies never prevent ROS startup."""

    def __init__(self, model_path: str, device: str, compute_type: str) -> None:
        self._model_path = model_path
        self._device = device
        self._compute_type = compute_type
        self._model = None

    def transcribe_pcm(self, pcm: bytes) -> str:
        import numpy as np
        from faster_whisper import WhisperModel

        if self._model is None:
            try:
                self._model = WhisperModel(self._model_path, device=self._device, compute_type=self._compute_type)
            except Exception:
                self._model = WhisperModel(self._model_path, device="cpu", compute_type="int8")
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _ = self._model.transcribe(audio, language="zh")
        return "".join(segment.text for segment in segments).strip()


@dataclass
class PendingTask:
    kind: str
    waypoint: Waypoint
    item_name: str = ""
    grasp_target: str = ""
    grasp_timeout_sec: float | None = None
    immediate_reply: str = ""
    arrival_reply: str = ""
    success_reply: str = ""
    failure_reply: str = ""
    created_at: float = 0.0


@dataclass(frozen=True)
class DemoMotionCommand:
    label: str
    linear: float
    angular: float
    duration_sec: float


class VoiceDialogNode(Node):
    def __init__(self) -> None:
        super().__init__("voice_dialog_node")
        self._declare_parameters()
        self._target_frame = str(self.get_parameter("target_frame").value).lstrip("/")
        self._base_frame = str(self.get_parameter("base_frame").value).lstrip("/")
        self._motion_enabled = bool(self.get_parameter("enable_motion").value)
        self._text_queue: queue.Queue[str] = queue.Queue()
        self._tts_queue: queue.Queue[str | None] = queue.Queue()
        self._stop_event = threading.Event()
        self._goal_handle = None
        self._grasp_goal_handle = None
        self._pending_task: PendingTask | None = None
        self._active_task: PendingTask | None = None
        self._demo_motion_thread: threading.Thread | None = None
        self._demo_motion_stop = threading.Event()
        self._demo_motion_lock = threading.Lock()
        self._map_seen = False
        self._scan_seen = False
        self._odom_seen = False
        self._startup_time = time.monotonic()
        self._pure_test_announced = False
        self._pure_test_mode = self._initial_pure_test_mode()

        share_dir = Path(get_package_share_directory("project_link_voice"))
        default_waypoints = share_dir / "data" / "default_waypoints.json"
        override = str(self.get_parameter("waypoints_override_file").value).strip()
        self._waypoints = WaypointStore(default_waypoints, Path(override).expanduser() if override else None)
        self._item_aliases = parse_aliases(list(self.get_parameter("grasp_target_aliases").value))

        self._llm = ToolCallingClient(
            bool(self.get_parameter("enable_llm_tools").value),
            str(self.get_parameter("llm_base_url").value),
            str(self.get_parameter("llm_model").value),
        )
        self._tts = VolcanoTts(
            resource_id=str(self.get_parameter("volcano_resource_id").value).strip() or None,
            speaker=str(self.get_parameter("volcano_speaker").value).strip() or None,
            sample_rate=int(self.get_parameter("tts_sample_rate").value),
            enabled=bool(self.get_parameter("tts_enabled").value),
        )
        self._tf_buffer = Buffer()
        self._tf_listener = TransformListener(self._tf_buffer, self)
        self._drive_client = ActionClient(self, DriveToPoint, "/voice/drive_to_point")
        self._visual_grasp_client = ActionClient(
            self,
            TrackAndGrasp,
            str(self.get_parameter("visual_grasp_action_name").value),
        )
        self._connect_arm_client = self.create_client(
            Trigger,
            str(self.get_parameter("visual_grasp_connect_service").value),
        )
        self._set_torque_client = self.create_client(
            SetBool,
            str(self.get_parameter("visual_grasp_torque_service").value),
        )
        self._stop_grasp_client = self.create_client(
            Trigger,
            str(self.get_parameter("visual_grasp_stop_service").value),
        )
        self._tts_pub = self.create_publisher(String, "/voice/tts_text", 10)
        self._status_pub = self.create_publisher(String, "/voice/status", 10)
        self._demo_cmd_pub = self.create_publisher(Twist, str(self.get_parameter("demo_cmd_vel_topic").value), 10)
        self.create_subscription(String, "/voice/text_input", self._on_text_input, 10)
        self.create_subscription(OccupancyGrid, "/map", self._on_map, 10)
        self.create_subscription(LaserScan, "/scan", self._on_scan, 10)
        self.create_subscription(Odometry, "/odom", self._on_odom, 10)
        self.create_timer(0.1, self._process_queues)
        self.create_timer(1.0, self._publish_status)
        self.create_timer(1.0, self._update_pure_test_mode)
        self.create_timer(1.0, self._expire_pending_task)

        if bool(self.get_parameter("enable_audio").value):
            self._audio_thread = threading.Thread(target=self._audio_loop, name="voice-audio", daemon=True)
            self._audio_thread.start()
        else:
            self._audio_thread = None

        mode = "MOTION ENABLED" if self._motion_enabled else "DRY RUN"
        llm_ready, llm_reason = self._llm.available()
        self.get_logger().warn(f"Voice LLM orchestrator starts in {mode}. LLM={llm_ready}: {llm_reason}.")
        if bool(self.get_parameter("enable_demo_motion").value):
            self.get_logger().warn("VOICE DEMO MOTION ENABLED: bounded local /cmd_vel commands are accepted without SLAM.")
        self.get_logger().warn("Physical E-stop remains mandatory; LLM never directly controls ROS actions.")
        self._announce_pure_test_mode_if_needed()

    def _declare_parameters(self) -> None:
        self.declare_parameter("enable_motion", False)
        self.declare_parameter("enable_audio", True)
        self.declare_parameter("wakeup_only", False)
        self.declare_parameter("pure_test_mode", "auto")
        self.declare_parameter("pure_test_auto_delay_sec", 3.0)
        self.declare_parameter("keyboard_wakeup", False)
        self.declare_parameter("wakeup_serial_port", "/dev/ttyUSB0")
        self.declare_parameter("wakeup_serial_baud", 115200)
        self.declare_parameter("wakeup_match_text", "aiui_event")
        self.declare_parameter("target_frame", "map")
        self.declare_parameter("base_frame", "base_footprint")
        self.declare_parameter("waypoints_override_file", "")
        self.declare_parameter("funvad_model", "fsmn-vad")
        self.declare_parameter("funvad_device", "cuda")
        self.declare_parameter("audio_sample_rate", 16000)
        self.declare_parameter("audio_chunk_ms", 200)
        self.declare_parameter("audio_pre_roll_ms", 400)
        self.declare_parameter("audio_no_speech_timeout_sec", 8.0)
        self.declare_parameter("audio_max_utterance_sec", 12.0)
        self.declare_parameter("audio_min_speech_sec", 0.30)
        self.declare_parameter("whisper_model", "small")
        self.declare_parameter("whisper_device", "cuda")
        self.declare_parameter("whisper_compute_type", "float16")
        self.declare_parameter("enable_llm_tools", True)
        self.declare_parameter("llm_base_url", "https://api.siliconflow.cn/v1")
        self.declare_parameter("llm_model", "Qwen/Qwen3-8B")
        self.declare_parameter("confirmation_timeout_sec", 30.0)
        self.declare_parameter("enable_demo_motion", False)
        self.declare_parameter("demo_cmd_vel_topic", "/cmd_vel")
        self.declare_parameter("demo_linear_mps", 0.08)
        self.declare_parameter("demo_angular_rps", 0.35)
        self.declare_parameter("demo_step_sec", 1.0)
        self.declare_parameter("demo_turn_sec", 1.2)
        self.declare_parameter("demo_spin_sec", 5.5)
        self.declare_parameter("tts_enabled", True)
        self.declare_parameter("tts_sample_rate", 24000)
        self.declare_parameter("volcano_resource_id", "")
        self.declare_parameter("volcano_speaker", "")
        self.declare_parameter("enable_visual_grasp", False)
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

    def _initial_pure_test_mode(self) -> bool:
        mode = str(self.get_parameter("pure_test_mode").value).strip().lower()
        if mode in ("on", "true", "1", "yes"):
            return True
        if mode in ("off", "false", "0", "no"):
            return False
        return False

    def _update_pure_test_mode(self) -> None:
        mode = str(self.get_parameter("pure_test_mode").value).strip().lower()
        if mode not in ("auto", ""):
            self._announce_pure_test_mode_if_needed()
            return
        topic_names = {name for name, _types in self.get_topic_names_and_types()}
        required_topics = {"/map", "/scan", "/odom"}
        if self._pure_test_mode and required_topics.issubset(topic_names):
            self._pure_test_mode = False
            self.get_logger().warn("SLAM/base topics appeared; leaving pure test mode.")
            return
        if self._pure_test_mode:
            self._announce_pure_test_mode_if_needed()
            return
        delay = float(self.get_parameter("pure_test_auto_delay_sec").value)
        if time.monotonic() - self._startup_time < delay:
            return
        if required_topics.isdisjoint(topic_names):
            self._pure_test_mode = True
            self._announce_pure_test_mode_if_needed()

    def _announce_pure_test_mode_if_needed(self) -> None:
        if not self._pure_test_mode or self._pure_test_announced:
            return
        self._pure_test_announced = True
        message = (
            "PURE TEST MODE: only the voice service appears to be running locally. "
            "Wakeup/audio/ASR/LLM/TTS can be tested; formal drive or grasp actions will not be executed."
        )
        self.get_logger().warn(message)
        print("\n=== Project LINK Voice Pure Test Mode ===", flush=True)
        print("Only the voice service appears to be running on this machine.", flush=True)
        if bool(self.get_parameter("enable_demo_motion").value):
            print("Demo motion is enabled: short local /cmd_vel commands can move the base.", flush=True)
        else:
            print("This mode tests wakeup/audio/ASR/LLM/TTS; it never sends motion or grasp actions.", flush=True)
        print("Publish text with: ros2 topic pub --once /voice/text_input std_msgs/msg/String \"data: '去客厅'\"", flush=True)
        print("================================================\n", flush=True)

    def _on_map(self, _message: OccupancyGrid) -> None:
        self._map_seen = True

    def _on_scan(self, _message: LaserScan) -> None:
        self._scan_seen = True

    def _on_odom(self, _message: Odometry) -> None:
        self._odom_seen = True

    def _on_text_input(self, message: String) -> None:
        text = message.data.strip()
        if text:
            self._text_queue.put(text)

    def _publish_status(self) -> None:
        ready, reason = self._slam_ready()
        if self._active_task:
            status = f"executing_{self._active_task.kind}"
        elif self._pending_task:
            status = f"awaiting_confirmation_{self._pending_task.kind}"
        else:
            status = "idle"
        mode = "pure_test" if self._pure_test_mode else "production"
        self._status_pub.publish(String(data=f"{status}; mode={mode}; slam_ready={ready}; {reason}"))

    def _slam_ready(self) -> tuple[bool, str]:
        if self._pure_test_mode:
            return False, "pure test mode: motion and grasp execution disabled"
        if not self._map_seen:
            return False, "waiting for /map"
        if not self._scan_seen:
            return False, "waiting for /scan"
        if not self._odom_seen:
            return False, "waiting for /odom"
        try:
            self._tf_buffer.lookup_transform(self._target_frame, self._base_frame, rclpy.time.Time())
        except TransformException:
            return False, f"waiting for TF {self._target_frame}->{self._base_frame}"
        return True, "SLAM and TF are ready"

    def _current_pose(self) -> tuple[float, float, float] | None:
        try:
            transform = self._tf_buffer.lookup_transform(self._target_frame, self._base_frame, rclpy.time.Time())
        except TransformException:
            return None
        translation = transform.transform.translation
        rotation = transform.transform.rotation
        yaw = math.atan2(
            2.0 * (rotation.w * rotation.z + rotation.x * rotation.y),
            1.0 - 2.0 * (rotation.y * rotation.y + rotation.z * rotation.z),
        )
        return translation.x, translation.y, yaw

    def _say(self, text: str) -> None:
        if not text:
            return
        self.get_logger().info(f"TTS: {text}")
        self._tts.speak(text)
        self._tts_pub.publish(String(data=text))

    def _process_queues(self) -> None:
        while not self._tts_queue.empty():
            item = self._tts_queue.get_nowait()
            if item is not None:
                self._say(item)
        while not self._text_queue.empty():
            self._handle_text(self._text_queue.get_nowait())

    def _expire_pending_task(self) -> None:
        if not self._pending_task:
            return
        timeout = float(self.get_parameter("confirmation_timeout_sec").value)
        if time.monotonic() - self._pending_task.created_at > timeout:
            task = self._pending_task
            self._pending_task = None
            self._say(f"{task.waypoint.name}的任务确认超时，已作废。请重新下达指令。")

    def _handle_text(self, text: str) -> None:
        normalized = text.strip()
        if contains_any(normalized, CANCEL_WORDS):
            self._cancel_everything("voice cancellation")
            self._stop_demo_motion()
            self._say("已取消当前任务，并请求底盘和机械臂停止。")
            return
        demo_command = self._parse_demo_motion(normalized)
        if demo_command:
            self._start_demo_motion(demo_command)
            return
        if self._pending_task:
            if contains_any(normalized, CONFIRM_WORDS):
                task = self._pending_task
                self._pending_task = None
                self._confirm_and_execute(task)
            else:
                self._say("当前有待确认任务。请说确认开始，或说取消。")
            return
        threading.Thread(target=self._run_llm_turn, args=(normalized,), daemon=True).start()

    def _run_llm_turn(self, text: str) -> None:
        stream_open = False
        streamed_any = False

        def on_text_chunk(chunk: str | None) -> None:
            nonlocal stream_open, streamed_any
            if chunk is None:
                self._tts.speak_stream_end()
                stream_open = False
                return
            if chunk.strip():
                if not stream_open:
                    self._tts.speak_stream_start()
                stream_open = True
                streamed_any = True
                self._tts.speak_stream_feed(chunk)

        result = self._llm.chat(text, self._handle_tool_call, on_text_chunk)
        if result.kind != "text" or not streamed_any:
            self._tts_queue.put(result.reply)

    def _parse_demo_motion(self, text: str) -> DemoMotionCommand | None:
        if not bool(self.get_parameter("enable_demo_motion").value):
            return None
        normalized = text.replace(" ", "")
        linear = float(self.get_parameter("demo_linear_mps").value)
        angular = float(self.get_parameter("demo_angular_rps").value)
        step_sec = float(self.get_parameter("demo_step_sec").value)
        turn_sec = float(self.get_parameter("demo_turn_sec").value)
        spin_sec = float(self.get_parameter("demo_spin_sec").value)

        if any(word in normalized for word in ("转个圈", "转一圈", "旋转一圈", "原地转圈")):
            return DemoMotionCommand("原地转一圈", 0.0, angular, spin_sec)
        if any(word in normalized for word in ("往前", "前进", "向前", "走两步", "走一步", "前走")):
            return DemoMotionCommand("前进一点", linear, 0.0, step_sec)
        if any(word in normalized for word in ("后退", "倒退", "往后", "退两步", "退一步")):
            return DemoMotionCommand("后退一点", -linear, 0.0, step_sec)
        if any(word in normalized for word in ("左转", "向左转", "往左转")):
            return DemoMotionCommand("左转一点", 0.0, angular, turn_sec)
        if any(word in normalized for word in ("右转", "向右转", "往右转")):
            return DemoMotionCommand("右转一点", 0.0, -angular, turn_sec)
        return None

    def _start_demo_motion(self, command: DemoMotionCommand) -> None:
        if self._active_task or self._pending_task:
            self._say("当前有正式任务，演示动作被拒绝。请先取消。")
            return
        if not bool(self.get_parameter("enable_demo_motion").value):
            self._say("演示运动模式未开启。")
            return
        with self._demo_motion_lock:
            self._stop_demo_motion_locked()
            self._demo_motion_stop.clear()
            self._demo_motion_thread = threading.Thread(
                target=self._run_demo_motion,
                args=(command,),
                name="voice-demo-motion",
                daemon=True,
            )
            self._demo_motion_thread.start()
        self._say(f"演示动作：{command.label}。")

    def _run_demo_motion(self, command: DemoMotionCommand) -> None:
        start = time.monotonic()
        rate_sec = 0.05
        twist = Twist()
        twist.linear.x = command.linear
        twist.angular.z = command.angular
        try:
            while not self._demo_motion_stop.is_set() and time.monotonic() - start < command.duration_sec:
                self._demo_cmd_pub.publish(twist)
                time.sleep(rate_sec)
        finally:
            self._publish_demo_stop()

    def _stop_demo_motion(self) -> None:
        with self._demo_motion_lock:
            self._stop_demo_motion_locked()

    def _stop_demo_motion_locked(self) -> None:
        self._demo_motion_stop.set()
        self._publish_demo_stop()
        thread = self._demo_motion_thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=0.5)
        if thread is self._demo_motion_thread:
            self._demo_motion_thread = None

    def _publish_demo_stop(self) -> None:
        stop = Twist()
        for _index in range(5):
            self._demo_cmd_pub.publish(stop)
            time.sleep(0.02)

    def _handle_tool_call(self, name: str, args: dict[str, Any]) -> ToolResult:
        if name == "get_weather":
            return ToolResult(self._tool_get_weather(args))
        if name == "get_current_location":
            return ToolResult(self._tool_get_current_location())
        if name == "save_waypoint":
            return ToolResult(self._tool_save_waypoint(args))
        if name == "list_saved_locations":
            return ToolResult({"success": True, "locations": self._waypoints.names()})
        if name == "cancel_current_task":
            self._cancel_everything("llm cancellation")
            return ToolResult({"success": True, "message": "已取消当前任务。"}, stop_after_tool=True, spoken_reply="已取消当前任务。")
        if name == "navigate_to_location":
            return self._tool_prepare_navigation(args)
        if name == "fetch_item_from_location":
            return self._tool_prepare_fetch(args)
        return ToolResult({"success": False, "message": f"未知工具：{name}"})

    def _tool_get_weather(self, args: dict[str, Any]) -> dict[str, Any]:
        city = str(args.get("city_name", "")).strip()
        if not city:
            return {"success": False, "message": "缺少城市名称。"}
        api_key = os.environ.get("QWEATHER_API_KEY")
        if not api_key:
            return {"success": False, "message": "天气 API 未配置 QWEATHER_API_KEY。"}
        try:
            encoded_city = urllib.parse.quote(city)
            lookup_url = f"https://geoapi.qweather.com/v2/city/lookup?location={encoded_city}&key={api_key}"
            with urllib.request.urlopen(lookup_url, timeout=5.0) as response:
                lookup = self._load_json_response(response)
            locations = lookup.get("location") or []
            if not locations:
                return {"success": False, "message": f"没有找到城市：{city}。"}
            location_id = str(locations[0]["id"])
            weather_url = f"https://devapi.qweather.com/v7/weather/now?location={location_id}&key={api_key}"
            with urllib.request.urlopen(weather_url, timeout=5.0) as response:
                weather = self._load_json_response(response)
            now = weather.get("now") or {}
            return {
                "success": True,
                "city": locations[0].get("name", city),
                "text": now.get("text", ""),
                "temp_c": now.get("temp", ""),
                "feels_like_c": now.get("feelsLike", ""),
                "wind_dir": now.get("windDir", ""),
                "wind_scale": now.get("windScale", ""),
                "humidity": now.get("humidity", ""),
            }
        except Exception as exc:
            return {"success": False, "message": f"天气查询失败：{exc}"}

    @staticmethod
    def _load_json_response(response) -> dict[str, Any]:
        import json

        data = response.read().decode("utf-8")
        value = json.loads(data)
        return value if isinstance(value, dict) else {}

    def _tool_get_current_location(self) -> dict[str, Any]:
        pose = self._current_pose()
        if pose is None:
            return {"success": False, "message": f"当前没有有效 TF {self._target_frame}->{self._base_frame}。"}
        x, y, yaw = pose
        return {"success": True, "frame": self._target_frame, "x": x, "y": y, "yaw": yaw}

    def _tool_save_waypoint(self, args: dict[str, Any]) -> dict[str, Any]:
        name = str(args.get("location_name", "")).strip()
        if not name:
            return {"success": False, "message": "地点名称不能为空。"}
        pose = self._current_pose()
        if pose is None:
            return {"success": False, "message": f"无法保存，TF {self._target_frame}->{self._base_frame} 不可用。"}
        try:
            self._waypoints.save(name, *pose)
        except Exception as exc:
            return {"success": False, "message": f"保存失败：{exc}"}
        return {"success": True, "message": f"已保存地点：{name}。"}

    def _tool_prepare_navigation(self, args: dict[str, Any]) -> ToolResult:
        if self._active_task:
            return ToolResult({"success": False, "message": "当前已有任务正在执行，请先取消或等待完成。"})
        waypoint = self._resolve_waypoint(args)
        if waypoint is None:
            return ToolResult({"success": False, "message": "目标地点不是已保存航点。"})
        task = PendingTask(
            kind="navigate",
            waypoint=waypoint,
            immediate_reply=str(args.get("immediate_reply") or f"确认后前往{waypoint.name}。"),
            arrival_reply=str(args.get("arrival_reply") or f"已到达{waypoint.name}。"),
            created_at=time.monotonic(),
        )
        self._pending_task = task
        spoken = (
            f"准备前往{waypoint.name}。本阶段是无避障低速直驱，必须确认路径清空、有人监护并且急停可用。"
            "请说确认开始，或说取消。"
        )
        return ToolResult({"success": True, "pending": "navigation", "target_name": waypoint.name}, True, spoken)

    def _tool_prepare_fetch(self, args: dict[str, Any]) -> ToolResult:
        if self._active_task:
            return ToolResult({"success": False, "message": "当前已有任务正在执行，请先取消或等待完成。"})
        waypoint = self._resolve_waypoint(args)
        if waypoint is None:
            return ToolResult({"success": False, "message": "抓取地点不是已保存航点。"})
        item_name = str(args.get("item_name") or "").strip()
        grasp_target = str(args.get("grasp_target") or "").strip()
        if not grasp_target and item_name:
            grasp_target = self._item_aliases.get(item_name, item_name)
        if not item_name or not grasp_target:
            return ToolResult({"success": False, "message": "取物任务缺少物品名称或视觉抓取目标。"})
        timeout_value = args.get("timeout_sec")
        timeout_sec = None
        if isinstance(timeout_value, (int, float)) and float(timeout_value) > 0:
            timeout_sec = float(timeout_value)
        task = PendingTask(
            kind="fetch",
            waypoint=waypoint,
            item_name=item_name,
            grasp_target=grasp_target,
            grasp_timeout_sec=timeout_sec,
            immediate_reply=str(args.get("immediate_reply") or f"确认后前往{waypoint.name}抓取{item_name}。"),
            arrival_reply=str(args.get("arrival_reply") or f"已到达{waypoint.name}，准备抓取{item_name}。"),
            success_reply=str(args.get("success_reply") or f"{item_name}抓取成功，我会停在当前位置。"),
            failure_reply=str(args.get("failure_reply") or f"{item_name}抓取失败，我会停在当前位置。"),
            created_at=time.monotonic(),
        )
        self._pending_task = task
        spoken = (
            f"准备前往{waypoint.name}抓取{item_name}，视觉目标是{grasp_target}。"
            "本阶段没有路径规划和避障，必须确认路径、底盘周围和机械臂区域都安全，急停可用。"
            "请说确认开始，或说取消。"
        )
        return ToolResult(
            {"success": True, "pending": "fetch", "target_name": waypoint.name, "grasp_target": grasp_target},
            True,
            spoken,
        )

    def _resolve_waypoint(self, args: dict[str, Any]) -> Waypoint | None:
        name = str(args.get("target_name") or args.get("location_name") or "").strip()
        return self._waypoints.get(name) if name else None

    def _confirm_and_execute(self, task: PendingTask) -> None:
        if self._active_task:
            self._say("当前已有任务正在执行，拒绝启动新的任务。")
            return
        if self._pure_test_mode:
            self._say(f"纯测试模式已确认{task.waypoint.name}任务；不会发送底盘或机械臂动作。")
            return
        ready, reason = self._slam_ready()
        if not ready:
            self._say(f"拒绝启动，{reason}。")
            return
        if not self._motion_enabled:
            self._say(f"Dry-run 已确认{task.waypoint.name}任务；未启用运动，不会发送底盘或机械臂动作。")
            return
        self._active_task = task
        self._send_drive_goal(task)

    def _send_drive_goal(self, task: PendingTask) -> None:
        if not self._drive_client.wait_for_server(timeout_sec=0.0):
            self._active_task = None
            self._say("直驱服务器未就绪，未启动运动。")
            return
        goal = DriveToPoint.Goal()
        goal.target = PoseStamped()
        goal.target.header.frame_id = self._target_frame
        goal.target.header.stamp = self.get_clock().now().to_msg()
        goal.target.pose.position.x = task.waypoint.x
        goal.target.pose.position.y = task.waypoint.y
        goal.target.pose.orientation.z = math.sin(task.waypoint.yaw / 2.0)
        goal.target.pose.orientation.w = math.cos(task.waypoint.yaw / 2.0)
        self._say(task.immediate_reply or f"确认前往{task.waypoint.name}，低速直驱已启动。")
        future = self._drive_client.send_goal_async(goal, feedback_callback=self._on_drive_feedback)
        future.add_done_callback(self._on_drive_goal_response)

    def _on_drive_goal_response(self, future) -> None:
        try:
            self._goal_handle = future.result()
        except Exception as exc:
            self._finish_active_task(f"直驱目标发送失败：{exc}")
            return
        if self._goal_handle is None:
            self._finish_active_task("直驱目标发送失败：无响应。")
            return
        if not self._goal_handle.accepted:
            self._finish_active_task("直驱服务器拒绝了目标，机器人没有运动。")
            return
        result_future = self._goal_handle.get_result_async()
        result_future.add_done_callback(self._on_drive_result)

    def _on_drive_feedback(self, feedback_message) -> None:
        feedback = feedback_message.feedback
        self.get_logger().info(f"Direct drive: {feedback.state}; remaining={feedback.distance_remaining:.2f}m")

    def _on_drive_result(self, future) -> None:
        self._goal_handle = None
        task = self._active_task
        if task is None:
            return
        try:
            result = future.result().result
        except Exception as exc:
            self._finish_active_task(f"直驱结果异常，取消后续任务：{exc}")
            return
        if result.status != 0:
            self._finish_active_task(f"导航未成功，取消后续任务：{result.message}")
            return
        if task.kind == "fetch":
            self._say(task.arrival_reply or f"已到达{task.waypoint.name}并停车，准备抓取{task.item_name}。")
            self._start_visual_grasp(task)
            return
        self._finish_active_task(task.arrival_reply or f"直驱结束：{result.message}", success=True)

    def _start_visual_grasp(self, task: PendingTask) -> None:
        if not bool(self.get_parameter("enable_visual_grasp").value):
            self._finish_active_task(
                f"视觉抓取未启用，已停在{task.waypoint.name}。确认机械臂安全后，用 enable_visual_grasp:=true 再运行取物流程。"
            )
            return
        if bool(self.get_parameter("visual_grasp_prepare_arm").value):
            self._prepare_arm_then_grasp(task)
        else:
            self._send_visual_grasp_goal(task)

    def _prepare_arm_then_grasp(self, task: PendingTask) -> None:
        if not self._connect_arm_client.wait_for_service(timeout_sec=0.0):
            self._finish_active_task("抓取流程未启动：connect_arm 服务不可用。")
            return
        future = self._connect_arm_client.call_async(Trigger.Request())
        future.add_done_callback(lambda done: self._on_connect_arm_done(done, task))

    def _on_connect_arm_done(self, future, task: PendingTask) -> None:
        response = self._service_response(future, "connect_arm")
        if response is None:
            return
        if not self._set_torque_client.wait_for_service(timeout_sec=0.0):
            self._finish_active_task("抓取流程未启动：set_torque 服务不可用。")
            return
        request = SetBool.Request()
        request.data = True
        torque_future = self._set_torque_client.call_async(request)
        torque_future.add_done_callback(lambda done: self._on_set_torque_done(done, task))

    def _on_set_torque_done(self, future, task: PendingTask) -> None:
        response = self._service_response(future, "set_torque")
        if response is None:
            return
        self._send_visual_grasp_goal(task)

    def _service_response(self, future, name: str):
        try:
            response = future.result()
        except Exception as exc:
            self._finish_active_task(f"{name} 调用异常：{exc}")
            return None
        if response is None or not response.success:
            message = response.message if response else "无响应"
            self._finish_active_task(f"{name} 调用失败：{message}")
            return None
        return response

    def _send_visual_grasp_goal(self, task: PendingTask) -> None:
        if not self._visual_grasp_client.wait_for_server(timeout_sec=0.0):
            self._finish_active_task("视觉抓取 Action 不可用。")
            return
        goal = TrackAndGrasp.Goal()
        goal.target = task.grasp_target
        goal.timeout_sec = task.grasp_timeout_sec or float(self.get_parameter("visual_grasp_timeout_sec").value)
        future = self._visual_grasp_client.send_goal_async(goal, feedback_callback=self._on_grasp_feedback)
        future.add_done_callback(self._on_grasp_goal_response)

    def _on_grasp_goal_response(self, future) -> None:
        try:
            self._grasp_goal_handle = future.result()
        except Exception as exc:
            self._finish_active_task(f"视觉抓取目标发送失败：{exc}")
            return
        if self._grasp_goal_handle is None or not self._grasp_goal_handle.accepted:
            self._finish_active_task("视觉抓取任务被拒绝。")
            return
        self._say("视觉抓取已开始。")
        result_future = self._grasp_goal_handle.get_result_async()
        result_future.add_done_callback(self._on_grasp_result)

    def _on_grasp_feedback(self, feedback_message) -> None:
        feedback = feedback_message.feedback
        self.get_logger().info(
            f"Visual grasp: state={feedback.state}; confidence={feedback.confidence:.2f}; {feedback.message}"
        )

    def _on_grasp_result(self, future) -> None:
        task = self._active_task
        try:
            result = future.result().result
        except Exception as exc:
            self._finish_active_task(f"视觉抓取结果异常：{exc}")
            return
        self._grasp_goal_handle = None
        if result.success:
            self._finish_active_task(task.success_reply if task else f"抓取成功，状态{result.final_state}。", success=True)
        else:
            message = task.failure_reply if task else "抓取失败。"
            self._finish_active_task(f"{message} 状态{result.final_state}，原因：{result.message}")

    def _finish_active_task(self, message: str, success: bool = False) -> None:
        self._active_task = None
        self._say(message)
        self._llm.append_system_event(f"Robot task finished. success={success}. message={message}")

    def _cancel_everything(self, reason: str) -> None:
        self._pending_task = None
        self._active_task = None
        self._stop_demo_motion()
        if self._goal_handle is not None:
            self.get_logger().warn(f"Canceling direct drive: {reason}")
            self._goal_handle.cancel_goal_async()
            self._goal_handle = None
        if self._grasp_goal_handle is not None:
            self.get_logger().warn(f"Canceling visual grasp: {reason}")
            self._grasp_goal_handle.cancel_goal_async()
            self._grasp_goal_handle = None
        if self._stop_grasp_client.wait_for_service(timeout_sec=0.0):
            self._stop_grasp_client.call_async(Trigger.Request())

    def _wait_for_wake_event(self) -> str:
        if bool(self.get_parameter("keyboard_wakeup").value):
            input("Press Enter to wake Project LINK voice service: ")
            return "keyboard"
        try:
            import serial
        except ImportError as exc:
            raise RuntimeError("pyserial is required for serial wakeup") from exc
        port = str(self.get_parameter("wakeup_serial_port").value)
        baud = int(self.get_parameter("wakeup_serial_baud").value)
        match_text = str(self.get_parameter("wakeup_match_text").value)
        with serial.Serial(port, baud, timeout=0.5) as serial_port:
            while not self._stop_event.is_set():
                data = serial_port.readline().strip()
                if data:
                    decoded = data.decode("utf-8", errors="backslashreplace")
                    if self._pure_test_mode:
                        print(f"WAKEUP raw={data!r} text={decoded}", flush=True)
                    if match_text and match_text not in decoded:
                        continue
                    return decoded
        return ""

    def _audio_loop(self) -> None:
        settings = VadSettings(
            sample_rate=int(self.get_parameter("audio_sample_rate").value),
            chunk_ms=int(self.get_parameter("audio_chunk_ms").value),
            pre_roll_ms=int(self.get_parameter("audio_pre_roll_ms").value),
            no_speech_timeout_sec=float(self.get_parameter("audio_no_speech_timeout_sec").value),
            max_utterance_sec=float(self.get_parameter("audio_max_utterance_sec").value),
            min_speech_sec=float(self.get_parameter("audio_min_speech_sec").value),
        )
        recorder = FunVadRecorder(settings, str(self.get_parameter("funvad_model").value), str(self.get_parameter("funvad_device").value))
        transcriber = WhisperTranscriber(
            str(self.get_parameter("whisper_model").value),
            str(self.get_parameter("whisper_device").value),
            str(self.get_parameter("whisper_compute_type").value),
        )
        while not self._stop_event.is_set():
            try:
                wake_event = self._wait_for_wake_event()
                if self._stop_event.is_set():
                    return
                if wake_event:
                    self.get_logger().info(f"Wakeup event: {wake_event}")
                if bool(self.get_parameter("wakeup_only").value):
                    self._say("纯测试模式收到唤醒信号。")
                    continue
                self._say("我在，请说。")
                pcm, reason = recorder.record()
                if reason == "no_speech_timeout":
                    self._say("没有听到有效语音，我先休息了。")
                    continue
                if not pcm:
                    self._say("录音结束，但没有有效语音。")
                    continue
                text = transcriber.transcribe_pcm(pcm)
                if text:
                    self.get_logger().info(f"ASR: {text}")
                    self._text_queue.put(text)
                else:
                    self._say("没有识别到有效指令。")
            except Exception as exc:
                self.get_logger().error(f"Audio loop failed: {exc}")
                self._say("语音输入不可用，请检查麦克风、串口和模型依赖。")
                self._stop_event.wait(2.0)

    def destroy_node(self):
        self._stop_event.set()
        self._stop_demo_motion()
        self._tts.shutdown()
        self._cancel_everything("node shutdown")
        return super().destroy_node()


def main() -> None:
    rclpy.init()
    node = VoiceDialogNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
