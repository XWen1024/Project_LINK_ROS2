#!/usr/bin/env python3
"""Standalone LLM + TTS voice car demo with bounded /cmd_vel tools."""

from __future__ import annotations

import json
import math
import os
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any

import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node
from std_msgs.msg import String

from .funvad import FunVadRecorder, VadSettings
from .volcano_tts import VolcanoTts


DEMO_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "demo_motion",
            "description": "Make the robot perform one short bounded motion demo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["forward", "backward", "turn_left", "turn_right", "spin", "stop"],
                        "description": "The short demo action to execute.",
                    },
                    "spoken_reply": {
                        "type": "string",
                        "description": "Short Chinese sentence to speak before executing the action.",
                    },
                },
                "required": ["action"],
            },
        },
    }
]


SYSTEM_PROMPT = """你是 Project LINK 的现场语音演示助手。
现在没有雷达、地图、航点、机械臂，也没有正式导航。
你只能通过 demo_motion 工具控制小车做短动作：
- forward: 前进一点
- backward: 后退一点
- turn_left: 左转一点
- turn_right: 右转一点
- spin: 原地转一圈
- stop: 停止

规则：
- 用户要求车动，就必须调用 demo_motion。
- 用户闲聊时可以直接短句回答。
- 不要提地图、导航、机械臂、抓取、避障。
- 回复要短，适合 TTS 播报。"""


@dataclass(frozen=True)
class MotionSpec:
    label: str
    linear: float
    angular: float
    duration_sec: float


class WhisperTranscriber:
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


class LlmMotionDemoNode(Node):
    def __init__(self) -> None:
        super().__init__("llm_motion_demo_node")
        self._declare_parameters()
        self._text_queue: queue.Queue[str] = queue.Queue()
        self._stop_event = threading.Event()
        self._motion_stop = threading.Event()
        self._motion_thread: threading.Thread | None = None
        self._motion_lock = threading.Lock()
        self._llm_history: list[dict[str, Any]] = []
        self._openai_client = None

        self._cmd_pub = self.create_publisher(Twist, str(self.get_parameter("cmd_vel_topic").value), 10)
        self._status_pub = self.create_publisher(String, "/voice_demo/status", 10)
        self.create_subscription(String, "/voice_demo/text_input", self._on_text_input, 10)
        self.create_timer(0.1, self._process_text_queue)

        self._tts = VolcanoTts(
            resource_id=str(self.get_parameter("volcano_resource_id").value).strip() or None,
            speaker=str(self.get_parameter("volcano_speaker").value).strip() or None,
            sample_rate=int(self.get_parameter("tts_sample_rate").value),
            enabled=bool(self.get_parameter("tts_enabled").value),
        )

        if bool(self.get_parameter("enable_audio").value):
            self._audio_thread = threading.Thread(target=self._audio_loop, name="llm-motion-demo-audio", daemon=True)
            self._audio_thread.start()
        else:
            self._audio_thread = None

        self.get_logger().warn("LLM VOICE CAR DEMO MODE: no SLAM, no waypoints, no arm; bounded /cmd_vel only.")
        self.get_logger().warn("Topics: subscribe /voice_demo/text_input, publish /voice_demo/status and /cmd_vel.")
        self._say("语音演示模式已启动。可以说前进、后退、左转、右转、转一圈，或停止。")

    def _declare_parameters(self) -> None:
        self.declare_parameter("cmd_vel_topic", "/cmd_vel")
        self.declare_parameter("enable_audio", True)
        self.declare_parameter("keyboard_wakeup", True)
        self.declare_parameter("wakeup_serial_port", "auto")
        self.declare_parameter("wakeup_serial_baud", 115200)
        self.declare_parameter("wakeup_match_text", "aiui_event")
        self.declare_parameter("llm_base_url", "https://api.siliconflow.cn/v1")
        self.declare_parameter("llm_model", "Qwen/Qwen3-8B")
        self.declare_parameter("tts_enabled", True)
        self.declare_parameter("tts_sample_rate", 24000)
        self.declare_parameter("volcano_resource_id", "")
        self.declare_parameter("volcano_speaker", "")
        self.declare_parameter("funvad_model", "fsmn-vad")
        self.declare_parameter("funvad_device", "cuda")
        self.declare_parameter("audio_sample_rate", 16000)
        self.declare_parameter("audio_chunk_ms", 200)
        self.declare_parameter("audio_pre_roll_ms", 400)
        self.declare_parameter("audio_no_speech_timeout_sec", 8.0)
        self.declare_parameter("audio_max_utterance_sec", 10.0)
        self.declare_parameter("audio_min_speech_sec", 0.30)
        self.declare_parameter("audio_input_device_index", -1)
        self.declare_parameter("whisper_model", "small")
        self.declare_parameter("whisper_device", "cuda")
        self.declare_parameter("whisper_compute_type", "float16")
        self.declare_parameter("demo_linear_mps", 0.06)
        self.declare_parameter("demo_angular_rps", 0.30)
        self.declare_parameter("demo_step_sec", 1.0)
        self.declare_parameter("demo_turn_sec", 1.2)
        self.declare_parameter("demo_spin_sec", 5.5)

    def _on_text_input(self, message: String) -> None:
        text = message.data.strip()
        if text:
            self._text_queue.put(text)

    def _process_text_queue(self) -> None:
        while not self._text_queue.empty():
            text = self._text_queue.get_nowait()
            self._status(f"heard: {text}")
            threading.Thread(target=self._handle_text, args=(text,), daemon=True).start()

    def _handle_text(self, text: str) -> None:
        if self._local_stop_requested(text):
            self._stop_motion()
            self._say("已停止。")
            return
        api_key = os.environ.get("SILICONFLOW_API_KEY")
        if not api_key:
            if not self._try_local_fallback(text):
                self._say("大模型密钥没有配置。可以说前进、后退、左转、右转、转个圈或停止。")
            return
        try:
            from openai import OpenAI

            if self._openai_client is None:
                self._openai_client = OpenAI(api_key=api_key, base_url=str(self.get_parameter("llm_base_url").value))
            messages = [{"role": "system", "content": SYSTEM_PROMPT}] + self._llm_history + [{"role": "user", "content": text}]
            response = self._openai_client.chat.completions.create(
                model=str(self.get_parameter("llm_model").value),
                messages=messages,
                tools=DEMO_TOOLS,
                tool_choice="auto",
                temperature=0.2,
                max_tokens=300,
            )
            message = response.choices[0].message
            self._llm_history.append({"role": "user", "content": text})
            assistant_message = {"role": "assistant", "content": message.content or ""}
            if message.tool_calls:
                assistant_message["tool_calls"] = [
                    {
                        "id": tool_call.id,
                        "type": "function",
                        "function": {
                            "name": tool_call.function.name,
                            "arguments": tool_call.function.arguments,
                        },
                    }
                    for tool_call in message.tool_calls
                ]
            self._llm_history.append(assistant_message)
            self._trim_history()

            if message.tool_calls:
                for tool_call in message.tool_calls:
                    if tool_call.function.name == "demo_motion":
                        args = self._parse_json_args(tool_call.function.arguments)
                        self._execute_tool_motion(args)
                        self._llm_history.append(
                            {"role": "tool", "tool_call_id": tool_call.id, "content": json.dumps({"success": True}, ensure_ascii=False)}
                        )
                return
            reply = (message.content or "").strip()
            if reply:
                self._say(reply)
        except Exception as exc:
            self.get_logger().error(f"LLM demo failed: {exc}")
            if not self._try_local_fallback(text):
                self._say("大模型暂时不可用，但本地前进后退转圈还可以用。")

    def _execute_tool_motion(self, args: dict[str, Any]) -> None:
        action = str(args.get("action", "")).strip()
        reply = str(args.get("spoken_reply", "")).strip()
        spec = self._motion_spec(action)
        if spec is None:
            self._say("这个动作我不能执行。")
            return
        if reply:
            self._say(reply)
        else:
            self._say(f"执行{spec.label}。")
        if action == "stop":
            self._stop_motion()
        else:
            self._start_motion(spec)

    def _try_local_fallback(self, text: str) -> bool:
        normalized = text.replace(" ", "")
        pairs = [
            (("转个圈", "转一圈", "旋转一圈", "原地转圈"), "spin"),
            (("往前", "前进", "向前", "走两步", "走一步", "前走"), "forward"),
            (("后退", "倒退", "往后", "退两步", "退一步"), "backward"),
            (("左转", "向左转", "往左转"), "turn_left"),
            (("右转", "向右转", "往右转"), "turn_right"),
        ]
        for words, action in pairs:
            if any(word in normalized for word in words):
                spec = self._motion_spec(action)
                if spec:
                    self._say(f"执行{spec.label}。")
                    self._start_motion(spec)
                    return True
        return False

    def _motion_spec(self, action: str) -> MotionSpec | None:
        linear = float(self.get_parameter("demo_linear_mps").value)
        angular = float(self.get_parameter("demo_angular_rps").value)
        step_sec = float(self.get_parameter("demo_step_sec").value)
        turn_sec = float(self.get_parameter("demo_turn_sec").value)
        spin_sec = float(self.get_parameter("demo_spin_sec").value)
        specs = {
            "forward": MotionSpec("前进一点", linear, 0.0, step_sec),
            "backward": MotionSpec("后退一点", -linear, 0.0, step_sec),
            "turn_left": MotionSpec("左转一点", 0.0, angular, turn_sec),
            "turn_right": MotionSpec("右转一点", 0.0, -angular, turn_sec),
            "spin": MotionSpec("原地转一圈", 0.0, angular, spin_sec),
            "stop": MotionSpec("停止", 0.0, 0.0, 0.0),
        }
        return specs.get(action)

    def _start_motion(self, spec: MotionSpec) -> None:
        with self._motion_lock:
            self._stop_motion_locked()
            self._motion_stop.clear()
            self._motion_thread = threading.Thread(target=self._run_motion, args=(spec,), daemon=True)
            self._motion_thread.start()
        self._status(f"motion: {spec.label}")

    def _run_motion(self, spec: MotionSpec) -> None:
        twist = Twist()
        twist.linear.x = spec.linear
        twist.angular.z = spec.angular
        start = time.monotonic()
        try:
            while not self._motion_stop.is_set() and time.monotonic() - start < spec.duration_sec:
                self._cmd_pub.publish(twist)
                time.sleep(0.05)
        finally:
            self._publish_stop()
            self._status("motion: stopped")

    def _stop_motion(self) -> None:
        with self._motion_lock:
            self._stop_motion_locked()

    def _stop_motion_locked(self) -> None:
        self._motion_stop.set()
        self._publish_stop()
        thread = self._motion_thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=0.5)
        if thread is self._motion_thread:
            self._motion_thread = None

    def _publish_stop(self) -> None:
        stop = Twist()
        for _index in range(5):
            self._cmd_pub.publish(stop)
            time.sleep(0.02)

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
                self.get_logger().info(f"Wakeup event: {wake_event}")
                self._say("我在，请说。")
                pcm, reason = recorder.record()
                if reason == "no_speech_timeout":
                    self._say("没有听到有效语音。")
                    continue
                if not pcm:
                    self._say("没有录到有效语音。")
                    continue
                text = transcriber.transcribe_pcm(pcm)
                if text:
                    self._status(f"asr: {text}")
                    self._text_queue.put(text)
                else:
                    self._say("没有识别到。")
            except Exception as exc:
                self.get_logger().error(f"Audio loop failed: {exc}")
                self._say("语音输入不可用，请检查串口、麦克风和模型。")
                self._stop_event.wait(2.0)

    def _wait_for_wake_event(self) -> str:
        if bool(self.get_parameter("keyboard_wakeup").value):
            input("Press Enter to wake voice car demo: ")
            return "keyboard"
        try:
            import serial
        except ImportError as exc:
            raise RuntimeError("pyserial is required for serial wakeup") from exc
        port = self._resolve_serial_port()
        baud = int(self.get_parameter("wakeup_serial_baud").value)
        match_text = str(self.get_parameter("wakeup_match_text").value)
        with serial.Serial(port, baud, timeout=0.5) as serial_port:
            while not self._stop_event.is_set():
                data = serial_port.readline().strip()
                if data:
                    decoded = data.decode("utf-8", errors="backslashreplace")
                    print(f"WAKEUP raw={data!r} text={decoded}", flush=True)
                    if match_text and match_text not in decoded:
                        continue
                    return decoded
        return ""

    def _resolve_serial_port(self) -> str:
        configured = str(self.get_parameter("wakeup_serial_port").value).strip()
        if configured and configured.lower() != "auto":
            return configured
        try:
            from serial.tools import list_ports

            ports = list(list_ports.comports())
        except Exception:
            ports = []
        if not ports:
            return "/dev/ttyUSB0"
        self.get_logger().warn("Serial ports: " + ", ".join(f"{port.device} {port.description}" for port in ports))
        preferred = [port for port in ports if "USB" in (port.description or "").upper() or "串行" in (port.description or "")]
        return (preferred or ports)[0].device

    def _say(self, text: str) -> None:
        if not text:
            return
        self.get_logger().info(f"TTS: {text}")
        self._tts.speak(text)
        self._status(f"tts: {text}")

    def _status(self, text: str) -> None:
        self._status_pub.publish(String(data=text))

    @staticmethod
    def _parse_json_args(raw: str) -> dict[str, Any]:
        try:
            value = json.loads(raw or "{}")
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _local_stop_requested(text: str) -> bool:
        normalized = text.replace(" ", "")
        return any(word in normalized for word in ("停止", "取消", "别动", "停下", "急停"))

    def _trim_history(self) -> None:
        if len(self._llm_history) > 16:
            self._llm_history = self._llm_history[-16:]

    def destroy_node(self):
        self._stop_event.set()
        self._stop_motion()
        self._tts.shutdown()
        return super().destroy_node()


def main() -> None:
    rclpy.init()
    node = LlmMotionDemoNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
