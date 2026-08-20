"""Normalize current classic and Qwen voice status payloads."""

from __future__ import annotations

from dataclasses import dataclass
import json


def active_voice_backend(classic_active: bool, qwen_active: bool) -> str:
    if qwen_active:
        return "qwen_realtime"
    if classic_active:
        return "classic"
    return "off"


@dataclass(frozen=True)
class VoiceState:
    backend: str = "off"
    state: str = "unknown"
    conversation_active: bool = False
    pending_task: str = ""
    active_task: str = ""

    @property
    def idle(self) -> bool:
        return not self.conversation_active and not self.pending_task and not self.active_task and self.state in {
            "idle",
            "unknown",
        }


def parse_voice_status(payload: str) -> VoiceState:
    text = payload.strip()
    if not text:
        return VoiceState()
    if text.startswith("{"):
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            return VoiceState(state="invalid")
        return VoiceState(
            backend=str(value.get("backend", "qwen_realtime")),
            state="conversation_active" if value.get("conversation_active") else "idle",
            conversation_active=bool(value.get("conversation_active")),
            pending_task=str(value.get("pending_task", "")),
            active_task=str(value.get("active_task", "")),
        )
    first, *_rest = text.split(";", 1)
    state = first.strip() or "unknown"
    return VoiceState(
        backend="classic",
        state=state,
        conversation_active=state == "conversation_active",
        pending_task=state.removeprefix("awaiting_confirmation_") if state.startswith("awaiting_confirmation_") else "",
        active_task=state.removeprefix("executing_") if state.startswith("executing_") else "",
    )
