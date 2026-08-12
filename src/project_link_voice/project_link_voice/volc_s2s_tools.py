"""Fail-closed local tools and WebSocket Function Calling message helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FunctionCall:
    call_id: str
    name: str
    arguments: str


def _json_text(value: Any, default: str = "{}") -> str:
    if isinstance(value, str):
        return value or default
    if value is None:
        return default
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def function_call_from_item(item: Any) -> FunctionCall | None:
    if not isinstance(item, dict) or item.get("type") != "function_call":
        return None
    call_id = str(item.get("call_id", "")).strip()
    name = str(item.get("name", "")).strip()
    if not call_id or not name:
        return None
    return FunctionCall(call_id, name, _json_text(item.get("arguments")))


def function_call_from_arguments_done(
    root: dict[str, Any],
    fallback: FunctionCall | None = None,
) -> FunctionCall | None:
    call_id = str(root.get("call_id", fallback.call_id if fallback else "")).strip()
    name = str(root.get("name", fallback.name if fallback else "")).strip()
    arguments = _json_text(
        root.get("arguments", fallback.arguments if fallback else "{}")
    )
    if not call_id or not name:
        return None
    return FunctionCall(call_id, name, arguments)


def function_calls_from_legacy_array(root: dict[str, Any]) -> list[FunctionCall]:
    calls: list[FunctionCall] = []
    raw_calls = root.get("tool_calls")
    if not isinstance(raw_calls, list):
        return calls
    for raw in raw_calls:
        if not isinstance(raw, dict):
            continue
        function = raw.get("function")
        if not isinstance(function, dict):
            continue
        call_id = str(raw.get("id", "")).strip()
        name = str(function.get("name", "")).strip()
        if call_id and name:
            calls.append(FunctionCall(call_id, name, _json_text(function.get("arguments"))))
    return calls


def execute_safe_function(call: FunctionCall) -> dict[str, Any]:
    """Execute only explicit side-effect-free tools in the first integration."""
    try:
        arguments = json.loads(call.arguments or "{}")
    except json.JSONDecodeError:
        return {"error": "invalid_arguments_json", "function": call.name}
    if not isinstance(arguments, dict):
        return {"error": "arguments_must_be_object", "function": call.name}
    if call.name == "get_magic_number":
        return {"number": 42}
    return {"error": "unsupported_function", "function": call.name}


def build_function_output_event(call_id: str, output: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "conversation.item.create",
        "item": {
            "call_id": call_id,
            "type": "function_call_output",
            "object": "realtime.item",
            "output": json.dumps(output, ensure_ascii=False, separators=(",", ":")),
        },
    }


def build_followup_response_event() -> dict[str, Any]:
    return {
        "type": "response.create",
        "response": {"modalities": ["text", "audio"]},
    }
