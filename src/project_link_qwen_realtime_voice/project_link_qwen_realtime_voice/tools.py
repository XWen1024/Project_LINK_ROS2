"""Realtime tool schemas and safety state."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

from project_link_voice.waypoints import Waypoint


BASE_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查询指定城市的实时天气。",
            "parameters": {
                "type": "object",
                "properties": {"city_name": {"type": "string", "description": "城市名称"}},
                "required": ["city_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_location",
            "description": "读取机器人当前 map 坐标位姿。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_waypoint",
            "description": "把机器人当前 map 位姿保存为命名航点。",
            "parameters": {
                "type": "object",
                "properties": {"location_name": {"type": "string", "description": "航点名称"}},
                "required": ["location_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_saved_locations",
            "description": "列出所有已经保存的命名航点。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "navigate_to_location",
            "description": "准备前往一个已保存命名航点。该工具只创建待确认任务，不会直接运动。",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_name": {"type": "string", "description": "已保存航点名称，禁止坐标"},
                    "immediate_reply": {"type": "string"},
                    "arrival_reply": {"type": "string"},
                },
                "required": ["target_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_item_from_location",
            "description": "准备导航到命名航点并抓取一个目标。只创建待确认任务。",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_name": {"type": "string"},
                    "item_name": {"type": "string"},
                    "grasp_target": {"type": "string", "description": "YOLO World 英文目标文本"},
                    "timeout_sec": {"type": "number"},
                    "immediate_reply": {"type": "string"},
                    "arrival_reply": {"type": "string"},
                    "success_reply": {"type": "string"},
                    "failure_reply": {"type": "string"},
                },
                "required": ["target_name", "item_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_current_task",
            "description": "取消当前待确认或正在执行的机器人任务。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


DEMO_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "demo_motion",
        "description": "仅在演示模式中执行一个短时、低速的底盘动作。",
        "parameters": {
            "type": "object",
            "properties": {
                "direction": {
                    "type": "string",
                    "enum": ["forward", "backward", "left", "right", "spin", "stop"],
                }
            },
            "required": ["direction"],
        },
    },
}


SYSTEM_PROMPT = """你是 Project LINK 机器人的实时语音中枢。
你通过 Function Calling 使用机器人的能力，禁止声称尚未实际发生的动作。
天气、当前位置、保存航点、列出航点、导航、取物和取消必须调用对应工具。
导航和取物只允许使用已经保存的命名航点，禁止编造坐标。
navigate_to_location 和 fetch_item_from_location 只会建立待确认任务；Python 安全层会要求用户明确说“确认开始”。
模型绝不能绕过确认、直接控制 ROS Action、直接发布 cmd_vel 或启用机械臂扭矩。
工具结果包含 spoken_reply 时，只朗读 spoken_reply，不增加承诺或修改安全警告。
需要工具时不要先说自然语言。回复简短、自然，适合中文语音播报。
自定义工具已开启，不要使用原生联网搜索。"""


CONFIRM_PHRASES = {
    "确认",
    "确认开始",
    "确定",
    "确定开始",
    "好的确认开始",
    "是的确认开始",
    "可以开始",
}


EXIT_PHRASES = {
    "停止",
    "停下",
    "停一下",
    "急停",
    "别动",
    "取消",
    "退出",
    "退出对话",
    "结束对话",
    "退下",
    "休息",
    "休息吧",
    "不用了",
    "不要了",
    "算了",
    "别说了",
    "再见",
    "拜拜",
}

EXIT_PREFIXES = ("麻烦你", "麻烦", "好的", "好了", "那就", "可以", "请", "你")
EXIT_SUFFIXES = ("语音服务", "语音对话", "对话", "聊天", "一下吧", "一下", "吧", "了")


def tool_schemas(enable_demo_motion: bool) -> list[dict[str, Any]]:
    schemas = list(BASE_TOOL_SCHEMAS)
    if enable_demo_motion:
        schemas.append(DEMO_TOOL_SCHEMA)
    return schemas


def normalize_spoken_text(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", text).lower()


def is_explicit_confirmation(text: str) -> bool:
    return normalize_spoken_text(text) in CONFIRM_PHRASES


def is_explicit_exit(text: str) -> bool:
    normalized = normalize_spoken_text(text)
    if normalized in EXIT_PHRASES:
        return True
    candidate = normalized
    changed = True
    while changed and candidate:
        changed = False
        for prefix in EXIT_PREFIXES:
            if candidate.startswith(prefix):
                candidate = candidate[len(prefix):]
                changed = True
                break
        for suffix in EXIT_SUFFIXES:
            if candidate.endswith(suffix):
                candidate = candidate[:-len(suffix)]
                changed = True
                break
    return candidate in EXIT_PHRASES


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

    @classmethod
    def now(cls, **kwargs) -> "PendingTask":
        return cls(created_at=time.monotonic(), **kwargs)


@dataclass(frozen=True)
class ToolExecutionResult:
    payload: dict[str, Any]
    spoken_reply: str = ""
    requires_confirmation: bool = False
    end_conversation: bool = False
