"""OpenAI-compatible LLM tool-calling client for the voice orchestrator."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Callable


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
            "description": "Create a pending direct-drive task to a saved named waypoint.",
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
            "description": "Create a pending task: drive to a saved waypoint, then visually grasp one object.",
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
                    "arrival_reply": {"type": "string", "description": "Short reply after arriving at the grasp pose."},
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

    def __init__(self, enabled: bool, base_url: str, model: str, max_history: int = 20) -> None:
        self._enabled = enabled
        self._base_url = base_url
        self._model = model
        self._max_history = max_history
        self._history: list[dict] = []
        self._client = None

    def available(self) -> tuple[bool, str]:
        if not self._enabled:
            return False, "LLM tool calling is disabled"
        if not os.environ.get("SILICONFLOW_API_KEY"):
            return False, "SILICONFLOW_API_KEY is not set"
        return True, "ready"

    def append_system_event(self, text: str) -> None:
        self._history.append({"role": "system", "content": text})
        self._trim_history()

    def chat(
        self,
        user_text: str,
        tool_handler: Callable[[str, dict], ToolResult],
        text_callback: Callable[[str | None], None] | None = None,
    ) -> LlmResult:
        ready, reason = self.available()
        if not ready:
            return LlmResult("text", f"LLM 工具调用不可用：{reason}。")

        try:
            from openai import OpenAI

            if self._client is None:
                self._client = OpenAI(api_key=os.environ["SILICONFLOW_API_KEY"], base_url=self._base_url)

            self._history.append({"role": "user", "content": user_text})
            self._trim_history()
            messages = [self._system_message()] + self._history

            for _iteration in range(5):
                content, tool_calls = self._stream_once(messages, text_callback)
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
                    args = self._parse_args(call["arguments"])
                    handled = tool_handler(call["name"], args)
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

    def _stream_once(
        self,
        messages: list[dict],
        text_callback: Callable[[str | None], None] | None,
    ) -> tuple[str, list[dict[str, str]]]:
        stream = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            tools=TOOL_SCHEMAS,
            tool_choice="auto",
            temperature=0.4,
            max_tokens=1024,
            stream=True,
        )

        content_parts: list[str] = []
        tool_calls: dict[int, dict[str, str]] = {}
        think_filter = ThinkFilter()
        saw_tool_call = False

        for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta is None:
                continue
            if delta.tool_calls:
                if not saw_tool_call:
                    saw_tool_call = True
                    if text_callback:
                        flushed = think_filter.flush()
                        if flushed:
                            text_callback(flushed)
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
                content_parts.append(delta.content)
                if text_callback and not saw_tool_call:
                    filtered = think_filter.process(delta.content)
                    if filtered:
                        text_callback(filtered)

        if text_callback and not saw_tool_call:
            flushed = think_filter.flush()
            if flushed:
                text_callback(flushed)
            text_callback(None)

        return "".join(content_parts), [tool_calls[index] for index in sorted(tool_calls)]

    def _system_message(self) -> dict:
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return {"role": "system", "content": SYSTEM_PROMPT.format(current_time=current_time)}

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
