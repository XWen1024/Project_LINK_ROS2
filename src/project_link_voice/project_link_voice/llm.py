"""OpenAI-compatible LLM tool-calling client for the voice orchestrator."""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from .voice_profile import configured_tool_schemas, prompt_for


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Query current weather for a city. Use this for weather questions only.",
            "parameters": {
                "type": "object",
                "properties": {"city_name": {"type": "string", "description": "City name, such as 北京 or 上海."}},
                "required": ["city_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_location",
            "description": "Report the robot's current map pose or nearest known location.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_waypoint",
            "description": "Save the robot's current map pose as a named waypoint.",
            "parameters": {
                "type": "object",
                "properties": {"location_name": {"type": "string", "description": "Waypoint name to save."}},
                "required": ["location_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "navigate_to_location",
            "description": "Create a pending navigation task to a saved named waypoint.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_name": {
                        "type": "string",
                        "description": "Saved waypoint name. Do not invent coordinates.",
                    },
                    "immediate_reply": {
                        "type": "string",
                        "description": "Short reply after the human confirms and motion starts.",
                    },
                    "arrival_reply": {
                        "type": "string",
                        "description": "Short reply after the robot physically arrives.",
                    },
                },
                "required": ["target_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_item_from_location",
            "description": "Create a pending task: navigate to a saved waypoint, then visually grasp one object.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_name": {"type": "string", "description": "Saved waypoint name."},
                    "item_name": {"type": "string", "description": "Object name spoken by the user."},
                    "grasp_target": {
                        "type": "string",
                        "description": "YOLO World target text, such as medicine bottle or red cup.",
                    },
                    "timeout_sec": {"type": "number", "description": "Visual grasp timeout in seconds."},
                    "immediate_reply": {"type": "string", "description": "Short reply after confirmed motion starts."},
                    "arrival_reply": {
                        "type": "string",
                        "description": "Short reply after arriving at the grasp pose.",
                    },
                    "success_reply": {"type": "string", "description": "Short reply after grasp success."},
                    "failure_reply": {"type": "string", "description": "Short reply after grasp failure."},
                },
                "required": ["target_name", "item_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_saved_locations",
            "description": "List saved named waypoints.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_current_task",
            "description": "Cancel the current pending or executing robot task.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


DEFAULT_LLM_API_KEY_ENV = "DEEPSEEK_API_KEY"
DEFAULT_LLM_BASE_URL = "https://api.deepseek.com"
DEFAULT_LLM_MODEL = "deepseek-v4-flash"


SYSTEM_PROMPT = """你是 Project LINK 机器人的语音中枢。
当前时间：{current_time}

你必须用工具表达机器人能力：
- 问天气、当前位置、保存/列出地点时调用对应工具。
- 用户要求去某个地点时调用 navigate_to_location。
- 用户要求去某个地点拿/抓/取某个物品时调用 fetch_item_from_location。
- 用户要求停止或取消时调用 cancel_current_task。

安全规则：
- 目标地点必须来自已保存命名航点；不要编造坐标。
- 你只负责选择工具和填写参数，不能声称已经运动、已经发布速度或已经抓取。
- 需要调用工具时，第一轮只输出工具调用，不要先输出任何自然语言。
- 运动和抓取都需要 Python 安全层二次确认。工具调用后不要再要求用户确认，Python 会播报固定确认语。
- 回复要短，适合 TTS 播报。"""


class ThinkFilter:
    """Strip Qwen-style <think> blocks while streaming."""

    def __init__(self) -> None:
        self.in_think = False
        self.buffer = ""

    def process(self, chunk: str) -> str:
        self.buffer += chunk
        output = ""
        while True:
            if not self.in_think:
                index = self.buffer.find("<think>")
                if index >= 0:
                    output += self.buffer[:index]
                    self.in_think = True
                    self.buffer = self.buffer[index + len("<think>") :]
                    continue
                partial = self.buffer.rfind("<")
                if partial >= 0 and "<think>".startswith(self.buffer[partial:]):
                    output += self.buffer[:partial]
                    self.buffer = self.buffer[partial:]
                    break
                output += self.buffer
                self.buffer = ""
                break
            index = self.buffer.find("</think>")
            if index >= 0:
                self.in_think = False
                self.buffer = self.buffer[index + len("</think>") :]
                continue
            partial = self.buffer.rfind("</")
            if partial >= 0 and "</think>".startswith(self.buffer[partial:]):
                self.buffer = self.buffer[partial:]
            else:
                self.buffer = ""
            break
        return output

    def flush(self) -> str:
        if self.in_think:
            return ""
        output = self.buffer
        self.buffer = ""
        return output


class StreamingTextEmitter:
    """Emit short text batches at punctuation, size, or a strict time bound."""

    def __init__(
        self,
        callback: Callable[[str], None],
        max_delay_sec: float = 0.08,
        max_chars: int = 12,
        punctuation: str = "，。！？；\n",
    ) -> None:
        self._callback = callback
        self._max_delay_sec = max(0.0, float(max_delay_sec))
        self._max_chars = max(1, int(max_chars))
        self._punctuation = punctuation
        self._lock = threading.Lock()
        self._pending = ""
        self._timer: threading.Timer | None = None
        self._closed = False

    def feed(self, text: str) -> None:
        if not text:
            return
        with self._lock:
            if self._closed:
                return
            self._pending += text
            if len(self._pending) >= self._max_chars or any(
                mark in self._pending for mark in self._punctuation
            ):
                self._emit_locked()
            elif self._timer is None:
                self._timer = threading.Timer(self._max_delay_sec, self._on_timer)
                self._timer.daemon = True
                self._timer.start()

    def finish(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._emit_locked()
            self._closed = True

    def cancel(self) -> None:
        with self._lock:
            self._closed = True
            self._pending = ""
            self._cancel_timer_locked()

    def _on_timer(self) -> None:
        with self._lock:
            self._timer = None
            if not self._closed:
                self._emit_locked()

    def _emit_locked(self) -> None:
        text = self._pending
        self._pending = ""
        self._cancel_timer_locked()
        if text:
            self._callback(text)

    def _cancel_timer_locked(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

@dataclass(frozen=True)
class ToolResult:
    content: dict
    stop_after_tool: bool = False
    spoken_reply: str | None = None


@dataclass(frozen=True)
class LlmResult:
    kind: str
    reply: str
    tool_name: str | None = None


class ToolCallingClient:
    """Small wrapper around OpenAI-compatible streaming tool calls."""

    def __init__(
        self,
        enabled: bool,
        base_url: str,
        model: str,
        max_history: int = 20,
        api_key_env: str = DEFAULT_LLM_API_KEY_ENV,
    ) -> None:
        self._enabled = enabled
        self._base_url = base_url
        self._model = model
        self._api_key_env = api_key_env.strip() or DEFAULT_LLM_API_KEY_ENV
        self._max_history = max_history
        self._history: list[dict] = []
        self._client = None
        self._tool_schemas = configured_tool_schemas(TOOL_SCHEMAS)
        self._system_prompt = prompt_for("classic", SYSTEM_PROMPT)

    def available(self) -> tuple[bool, str]:
        if not self._enabled:
            return False, "LLM tool calling is disabled"
        if not os.environ.get(self._api_key_env):
            return False, f"{self._api_key_env} is not set"
        return True, "ready"

    def append_system_event(self, text: str) -> None:
        self._history.append({"role": "system", "content": text})
        self._trim_history()

    def reset_history(self) -> None:
        self._history.clear()

    def chat(
        self,
        user_text: str,
        tool_handler: Callable[[str, dict], ToolResult],
        text_callback: Callable[[str | None], None] | None = None,
        timing_callback: Callable[[str, float, dict], None] | None = None,
        text_cancel_callback: Callable[[], None] | None = None,
    ) -> LlmResult:
        ready, reason = self.available()
        if not ready:
            return LlmResult("text", f"LLM 工具调用不可用：{reason}。")

        chat_started_at = time.perf_counter()
        try:
            from openai import OpenAI

            if self._client is None:
                self._client = OpenAI(api_key=os.environ[self._api_key_env], base_url=self._base_url)

            self._history.append({"role": "user", "content": user_text})
            self._trim_history()
            messages = [self._system_message()] + self._history

            for iteration in range(5):
                content, tool_calls = self._stream_once(
                    messages,
                    text_callback,
                    timing_callback,
                    text_cancel_callback,
                    iteration,
                )
                if not tool_calls:
                    reply = self._clean_text(content)
                    self._history.append({"role": "assistant", "content": reply})
                    return LlmResult("text", reply)

                assistant_message = {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": [
                        {
                            "id": call["id"],
                            "type": "function",
                            "function": {"name": call["name"], "arguments": call["arguments"]},
                        }
                        for call in tool_calls
                    ],
                }
                self._history.append(assistant_message)
                messages.append(assistant_message)

                for call in tool_calls:
                    parse_started_at = time.perf_counter()
                    args = self._parse_args(call["arguments"])
                    self._notify_timing(
                        timing_callback,
                        "llm_tool_arguments_parse",
                        parse_started_at,
                        {"tool": call["name"], "iteration": iteration},
                    )
                    tool_started_at = time.perf_counter()
                    handled = tool_handler(call["name"], args)
                    self._notify_timing(
                        timing_callback,
                        "python_tool",
                        tool_started_at,
                        {"tool": call["name"], "iteration": iteration},
                    )
                    tool_message = {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": json.dumps(handled.content, ensure_ascii=False),
                    }
                    self._history.append(tool_message)
                    messages.append(tool_message)
                    if handled.stop_after_tool:
                        reply = handled.spoken_reply or str(handled.content.get("message", "已进入待确认状态。"))
                        return LlmResult("command", reply, call["name"])

            return LlmResult("text", "这个请求需要的工具步骤太多，我先停一下，请重新说一遍。")
        except Exception as exc:
            return LlmResult("error", f"LLM 工具调用失败：{exc}")
        finally:
            self._notify_timing(timing_callback, "llm_total", chat_started_at, {})

    def _stream_once(
        self,
        messages: list[dict],
        text_callback: Callable[[str | None], None] | None,
        timing_callback: Callable[[str, float, dict], None] | None,
        text_cancel_callback: Callable[[], None] | None,
        iteration: int,
    ) -> tuple[str, list[dict[str, str]]]:
        request_started_at = time.perf_counter()
        success = False
        tool_calls: dict[int, dict[str, str]] = {}
        text_emitter: StreamingTextEmitter | None = None
        try:
            self._notify_timing(
                timing_callback,
                "llm_request_sent",
                request_started_at,
                {"iteration": iteration},
            )
            stream = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                tools=self._tool_schemas,
                tool_choice="auto",
                temperature=0.4,
                max_tokens=384,
                stream=True,
                extra_body={"thinking": {"type": "disabled"}},
            )

            content_parts: list[str] = []
            think_filter = ThinkFilter()
            saw_tool_call = False
            first_delta_reported = False
            first_text_reported = False
            text_emitter = StreamingTextEmitter(text_callback) if text_callback else None

            for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta is None:
                    continue
                if not first_delta_reported and (delta.content or delta.tool_calls):
                    first_delta_reported = True
                    self._notify_timing(
                        timing_callback,
                        "llm_first_delta",
                        request_started_at,
                        {"iteration": iteration},
                    )
                if delta.tool_calls:
                    if not saw_tool_call:
                        saw_tool_call = True
                        if text_emitter is not None:
                            text_emitter.cancel()
                        if text_cancel_callback is not None:
                            text_cancel_callback()
                        elif text_callback:
                            think_filter.flush()
                            text_callback(None)
                    for call_delta in delta.tool_calls:
                        index = call_delta.index
                        entry = tool_calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                        if call_delta.id:
                            entry["id"] = call_delta.id
                        if call_delta.function:
                            if call_delta.function.name:
                                entry["name"] = call_delta.function.name
                            if call_delta.function.arguments:
                                entry["arguments"] += call_delta.function.arguments
                if delta.content:
                    if not first_text_reported and delta.content.strip():
                        first_text_reported = True
                        self._notify_timing(
                            timing_callback,
                            "llm_first_text",
                            request_started_at,
                            {"iteration": iteration},
                        )
                    content_parts.append(delta.content)
                    if text_callback and not saw_tool_call:
                        filtered = think_filter.process(delta.content)
                        if filtered and text_emitter is not None:
                            text_emitter.feed(filtered)

            if text_callback and not saw_tool_call:
                flushed = think_filter.flush()
                if flushed and text_emitter is not None:
                    text_emitter.feed(flushed)
                if text_emitter is not None:
                    text_emitter.finish()
                text_callback(None)

            if tool_calls:
                self._notify_timing(
                    timing_callback,
                    "llm_tool_call_complete",
                    request_started_at,
                    {"iteration": iteration, "tool_call_count": len(tool_calls)},
                )

            success = True
            return "".join(content_parts), [tool_calls[index] for index in sorted(tool_calls)]
        finally:
            if not success and text_emitter is not None:
                text_emitter.cancel()
                if text_cancel_callback is not None:
                    text_cancel_callback()
            self._notify_timing(
                timing_callback,
                "llm_api_roundtrip",
                request_started_at,
                {"iteration": iteration, "success": success, "tool_call_count": len(tool_calls)},
            )

    @staticmethod
    def _notify_timing(
        callback: Callable[[str, float, dict], None] | None,
        phase: str,
        started_at: float,
        fields: dict,
    ) -> None:
        if callback is None:
            return
        try:
            callback(phase, (time.perf_counter() - started_at) * 1000.0, fields)
        except Exception:
            pass

    def _system_message(self) -> dict:
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return {
            "role": "system",
            "content": self._system_prompt.replace("{current_time}", current_time),
        }

    def _trim_history(self) -> None:
        if len(self._history) > self._max_history * 2:
            self._history = self._history[-self._max_history * 2 :]

    @staticmethod
    def _parse_args(raw: str) -> dict:
        try:
            value = json.loads(raw or "{}")
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _clean_text(text: str) -> str:
        return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
