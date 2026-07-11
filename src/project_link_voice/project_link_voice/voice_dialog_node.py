#!/usr/bin/env python3
"""ROS 2 voice dialog node with local confirmation before direct-drive goals."""

from __future__ import annotations

import math
import queue
import threading
import time
from pathlib import Path

import rclpy
from ament_index_python.packages import get_package_share_directory
from geometry_msgs.msg import PoseStamped
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
from .llm import ChatResponder
from .task_parser import FetchTask, parse_aliases, parse_fetch_task
from .waypoints import ConfirmationState, Waypoint, WaypointStore


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


class VoiceDialogNode(Node):
    def __init__(self) -> None:
        super().__init__("voice_dialog_node")
        self._declare_parameters()
        self._target_frame = str(self.get_parameter("target_frame").value).lstrip("/")
        self._base_frame = str(self.get_parameter("base_frame").value).lstrip("/")
        self._motion_enabled = bool(self.get_parameter("enable_motion").value)
        self._text_queue: queue.Queue[str] = queue.Queue()
        self._reply_queue: queue.Queue[str] = queue.Queue()
        self._stop_event = threading.Event()
        self._state = ConfirmationState()
        self._goal_handle = None
        self._result_future = None
        self._grasp_goal_handle = None
        self._pending_fetch_task: FetchTask | None = None
        self._active_fetch_task: FetchTask | None = None
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
        self._chat = ChatResponder(
            bool(self.get_parameter("enable_llm_chat").value),
            str(self.get_parameter("llm_base_url").value),
            str(self.get_parameter("llm_model").value),
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
        self._item_aliases = parse_aliases(list(self.get_parameter("grasp_target_aliases").value))
        self._tts_pub = self.create_publisher(String, "/voice/tts_text", 10)
        self._status_pub = self.create_publisher(String, "/voice/status", 10)
        self.create_subscription(String, "/voice/text_input", self._on_text_input, 10)
        self.create_subscription(OccupancyGrid, "/map", self._on_map, 10)
        self.create_subscription(LaserScan, "/scan", self._on_scan, 10)
        self.create_subscription(Odometry, "/odom", self._on_odom, 10)
        self.create_timer(0.1, self._process_text_queue)
        self.create_timer(1.0, self._publish_status)
        self.create_timer(1.0, self._update_pure_test_mode)

        if bool(self.get_parameter("enable_audio").value):
            self._audio_thread = threading.Thread(target=self._audio_loop, name="voice-audio", daemon=True)
            self._audio_thread.start()
        else:
            self._audio_thread = None
        mode = "MOTION ENABLED" if self._motion_enabled else "DRY RUN"
        self.get_logger().warn(f"Voice direct drive starts in {mode}. Physical E-stop remains mandatory.")
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
        self.declare_parameter("enable_llm_chat", False)
        self.declare_parameter("llm_base_url", "https://api.siliconflow.cn/v1")
        self.declare_parameter("llm_model", "Qwen/Qwen3-8B")
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
            "Wakeup/audio/text parsing can be tested, but no drive goal or /cmd_vel will be sent."
        )
        self.get_logger().warn(message)
        print("\n=== Project LINK Voice Pure Test Mode ===", flush=True)
        print("Only the voice service appears to be running on this machine.", flush=True)
        print("This mode tests wakeup/audio/text parsing only; it never sends motion commands.", flush=True)
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
        status = "driving" if self._state.driving else "awaiting_confirmation" if self._state.pending_waypoint else "idle"
        mode = "pure_test" if self._pure_test_mode else "production"
        self._status_pub.publish(String(data=f"{status}; mode={mode}; slam_ready={ready}; {reason}"))

    def _slam_ready(self) -> tuple[bool, str]:
        if self._pure_test_mode:
            return False, "pure test mode: SLAM/TF gate bypassed for non-motion testing"
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

    def _say(self, text: str) -> None:
        self.get_logger().info(f"TTS: {text}")
        self._tts_pub.publish(String(data=text))

    def _process_text_queue(self) -> None:
        while not self._reply_queue.empty():
            self._say(self._reply_queue.get_nowait())
        while not self._text_queue.empty():
            self._handle_text(self._text_queue.get_nowait())

    def _handle_text(self, text: str) -> None:
        if self._pending_fetch_task and any(word in text for word in ("确认", "确定", "开始", "好的", "是的")):
            fetch_task = self._pending_fetch_task
            self._pending_fetch_task = None
            self._confirm_fetch_task(fetch_task)
            return

        fetch_task = parse_fetch_task(text, self._waypoints, self._item_aliases)
        if fetch_task:
            self._pending_fetch_task = fetch_task
            self._state.pending_waypoint = None
            self._say(
                f"取物任务：先去{fetch_task.waypoint.name}，到达安全抓取位后抓取{fetch_task.spoken_item}。"
                "当前没有避障，请确认周围清空并保持急停可用。请说确认开始。"
            )
            return

        command, waypoint = self._state.consume(text, self._waypoints)
        if command == "cancel":
            self._cancel_drive("voice cancellation")
            self._cancel_grasp("voice cancellation")
            self._pending_fetch_task = None
            self._say("已取消，并已发送停车指令。")
        elif command == "target" and waypoint:
            self._say(f"目标是{waypoint.name}。当前是无避障直驱，请确认周围清空并保持急停可用。请说确认前往。")
        elif command == "confirm" and waypoint:
            if self._pure_test_mode:
                self._state.clear_drive()
                self._say(f"纯测试模式已确认目标{waypoint.name}；不会发送直驱目标，也不会发布速度命令。")
                return
            ready, reason = self._slam_ready()
            if not ready:
                self._state.clear_drive()
                self._say(f"拒绝启动，{reason}。")
                return
            if not self._motion_enabled:
                self._state.clear_drive()
                self._say(f"Dry-run 已确认目标{waypoint.name}；未启用运动，不会发布速度命令。")
                return
            self._send_drive_goal(waypoint)
        else:
            threading.Thread(target=self._respond_to_chat, args=(text,), daemon=True).start()

    def _confirm_fetch_task(self, task: FetchTask) -> None:
        if self._pure_test_mode:
            self._say(
                f"纯测试模式已确认取物任务：去{task.waypoint.name}抓取{task.grasp_target}。"
                "不会发送直驱目标，也不会调用机械臂。"
            )
            return
        if not self._motion_enabled:
            self._say(
                f"Dry-run 已确认取物任务：去{task.waypoint.name}抓取{task.grasp_target}；"
                "未启用运动，不会发送直驱目标或机械臂指令。"
            )
            return
        ready, reason = self._slam_ready()
        if not ready:
            self._say(f"拒绝启动取物任务，{reason}。")
            return
        self._active_fetch_task = task
        self._send_drive_goal(task.waypoint)

    def _respond_to_chat(self, text: str) -> None:
        response = self._chat.respond(text)
        if response:
            self._reply_queue.put(response)
        else:
            self._reply_queue.put("我只接受已保存地点和确认或取消命令。")

    def _send_drive_goal(self, waypoint: Waypoint) -> None:
        if not self._drive_client.wait_for_server(timeout_sec=0.0):
            self._state.clear_drive()
            self._active_fetch_task = None
            self._say("直驱服务器未就绪，未启动运动。")
            return
        goal = DriveToPoint.Goal()
        goal.target = PoseStamped()
        goal.target.header.frame_id = self._target_frame
        goal.target.header.stamp = self.get_clock().now().to_msg()
        goal.target.pose.position.x = waypoint.x
        goal.target.pose.position.y = waypoint.y
        goal.target.pose.orientation.z = math.sin(waypoint.yaw / 2.0)
        goal.target.pose.orientation.w = math.cos(waypoint.yaw / 2.0)
        self._say(f"确认前往{waypoint.name}，低速直驱已启动。")
        future = self._drive_client.send_goal_async(goal, feedback_callback=self._on_feedback)
        future.add_done_callback(self._on_goal_response)

    def _on_goal_response(self, future) -> None:
        try:
            self._goal_handle = future.result()
        except Exception as exc:
            self._state.clear_drive()
            self._active_fetch_task = None
            self._say(f"直驱目标发送失败：{exc}")
            return
        if self._goal_handle is None:
            self._state.clear_drive()
            self._active_fetch_task = None
            self._say("直驱目标发送失败：无响应。")
            return
        if not self._goal_handle.accepted:
            self._state.clear_drive()
            self._active_fetch_task = None
            self._say("直驱服务器拒绝了目标，机器人没有运动。")
            return
        self._result_future = self._goal_handle.get_result_async()
        self._result_future.add_done_callback(self._on_result)

    def _on_feedback(self, feedback_message) -> None:
        feedback = feedback_message.feedback
        self.get_logger().info(f"Direct drive: {feedback.state}; remaining={feedback.distance_remaining:.2f}m")

    def _on_result(self, future) -> None:
        self._state.clear_drive()
        self._goal_handle = None
        try:
            result = future.result().result
        except Exception as exc:
            self._active_fetch_task = None
            self._say(f"直驱结果异常，取消后续任务：{exc}")
            return
        fetch_task = self._active_fetch_task
        if fetch_task:
            if result.status == 0:
                self._say(f"已到达{fetch_task.waypoint.name}并停车，准备抓取{fetch_task.spoken_item}。")
                self._start_visual_grasp(fetch_task)
            else:
                self._active_fetch_task = None
                self._say(f"导航未成功，取消抓取：{result.message}")
            return
        self._say(f"直驱结束：{result.message}")

    def _cancel_drive(self, reason: str) -> None:
        self._state.clear_drive()
        if self._goal_handle is not None:
            self.get_logger().warn(f"Canceling direct drive: {reason}")
            self._goal_handle.cancel_goal_async()

    def _cancel_grasp(self, reason: str) -> None:
        self._active_fetch_task = None
        if self._grasp_goal_handle is not None:
            self.get_logger().warn(f"Canceling visual grasp: {reason}")
            self._grasp_goal_handle.cancel_goal_async()
        if self._stop_grasp_client.wait_for_service(timeout_sec=0.0):
            self._stop_grasp_client.call_async(Trigger.Request())

    def _start_visual_grasp(self, task: FetchTask) -> None:
        if not bool(self.get_parameter("enable_visual_grasp").value):
            self._active_fetch_task = None
            self._say(
                f"视觉抓取未启用，已停在{task.waypoint.name}。"
                "确认机械臂安全后，用 enable_visual_grasp:=true 再运行取物流程。"
            )
            return
        if bool(self.get_parameter("visual_grasp_prepare_arm").value):
            self._prepare_arm_then_grasp(task)
        else:
            self._send_visual_grasp_goal(task)

    def _prepare_arm_then_grasp(self, task: FetchTask) -> None:
        if not self._connect_arm_client.wait_for_service(timeout_sec=0.0):
            self._finish_grasp_failure("connect_arm 服务不可用")
            return
        future = self._connect_arm_client.call_async(Trigger.Request())
        future.add_done_callback(lambda done: self._on_connect_arm_done(done, task))

    def _on_connect_arm_done(self, future, task: FetchTask) -> None:
        response = self._service_response(future, "connect_arm")
        if response is None:
            return
        if not self._set_torque_client.wait_for_service(timeout_sec=0.0):
            self._finish_grasp_failure("set_torque 服务不可用")
            return
        request = SetBool.Request()
        request.data = True
        torque_future = self._set_torque_client.call_async(request)
        torque_future.add_done_callback(lambda done: self._on_set_torque_done(done, task))

    def _on_set_torque_done(self, future, task: FetchTask) -> None:
        response = self._service_response(future, "set_torque")
        if response is None:
            return
        self._send_visual_grasp_goal(task)

    def _service_response(self, future, name: str):
        try:
            response = future.result()
        except Exception as exc:
            self._finish_grasp_failure(f"{name} 调用异常: {exc}")
            return None
        if response is None or not response.success:
            message = response.message if response else "无响应"
            self._finish_grasp_failure(f"{name} 调用失败: {message}")
            return None
        return response

    def _send_visual_grasp_goal(self, task: FetchTask) -> None:
        if not self._visual_grasp_client.wait_for_server(timeout_sec=0.0):
            self._finish_grasp_failure("视觉抓取 Action 不可用")
            return
        goal = TrackAndGrasp.Goal()
        goal.target = task.grasp_target
        goal.timeout_sec = float(self.get_parameter("visual_grasp_timeout_sec").value)
        future = self._visual_grasp_client.send_goal_async(goal, feedback_callback=self._on_grasp_feedback)
        future.add_done_callback(self._on_grasp_goal_response)

    def _on_grasp_goal_response(self, future) -> None:
        try:
            self._grasp_goal_handle = future.result()
        except Exception as exc:
            self._finish_grasp_failure(f"视觉抓取目标发送失败: {exc}")
            return
        if self._grasp_goal_handle is None or not self._grasp_goal_handle.accepted:
            self._finish_grasp_failure("视觉抓取任务被拒绝")
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
        try:
            result = future.result().result
        except Exception as exc:
            self._finish_grasp_failure(f"视觉抓取结果异常: {exc}")
            return
        self._grasp_goal_handle = None
        self._active_fetch_task = None
        if result.success:
            self._say(f"抓取成功，状态{result.final_state}。可以继续放置或返回流程。")
        else:
            self._say(f"抓取失败，状态{result.final_state}，原因：{result.message}")

    def _finish_grasp_failure(self, message: str) -> None:
        self._grasp_goal_handle = None
        self._active_fetch_task = None
        self.get_logger().error(message)
        self._say(f"抓取流程未启动或失败：{message}")

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
        self._cancel_drive("node shutdown")
        self._cancel_grasp("node shutdown")
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
