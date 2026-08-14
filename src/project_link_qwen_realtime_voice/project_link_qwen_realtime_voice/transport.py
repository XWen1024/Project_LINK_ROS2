"""DashScope-backed Qwen Omni realtime transport."""

from __future__ import annotations

import base64
import importlib.metadata
import inspect
import json
import os
import threading
from collections.abc import Callable
from typing import Any

from .protocol import RealtimeEvent, normalize_event


EventCallback = Callable[[RealtimeEvent], None]


class DashScopeRealtimeTransport:
    def __init__(
        self,
        callback: EventCallback,
        endpoint: str,
        model: str,
        voice: str,
        instructions: str,
        tools: list[dict[str, Any]],
        input_sample_rate: int = 16000,
        output_sample_rate: int = 24000,
        vad_type: str = "semantic_vad",
        vad_threshold: float = 0.5,
        vad_silence_ms: int = 1200,
        prefix_padding_ms: int = 300,
    ) -> None:
        self._callback = callback
        self._endpoint = endpoint
        self._model = model
        self._voice = voice
        self._instructions = instructions
        self._tools = tools
        self._input_sample_rate = input_sample_rate
        self._output_sample_rate = output_sample_rate
        self._vad_type = vad_type
        self._vad_threshold = vad_threshold
        self._vad_silence_ms = vad_silence_ms
        self._prefix_padding_ms = prefix_padding_ms
        self._conversation = None
        self._lock = threading.RLock()

    @property
    def sdk_version(self) -> str:
        try:
            return importlib.metadata.version("dashscope")
        except importlib.metadata.PackageNotFoundError:
            return "missing"

    def available(self) -> tuple[bool, str]:
        if not os.environ.get("DASHSCOPE_API_KEY", "").strip():
            return False, "DASHSCOPE_API_KEY is missing"
        if not self._endpoint:
            return False, "QWEN_REALTIME_ENDPOINT is missing"
        try:
            import dashscope  # noqa: F401
        except Exception as exc:
            return False, f"dashscope unavailable: {exc}"
        return True, f"dashscope={self.sdk_version}"

    def connect(self) -> None:
        import dashscope
        from dashscope.audio.qwen_omni import (
            AudioFormatConfig,
            OmniRealtimeCallback,
            OmniRealtimeConversation,
        )

        parent = self

        class Callback(OmniRealtimeCallback):
            def on_open(self, *args) -> None:
                parent._callback(RealtimeEvent("transport.open", {"args": list(args)}))

            def on_close(self, *args) -> None:
                parent._callback(RealtimeEvent("transport.close", {"args": list(args)}))

            def on_error(self, *args) -> None:
                parent._callback(RealtimeEvent("transport.error", {"args": [str(value) for value in args]}))

            def on_event(self, response) -> None:
                parent._callback(normalize_event(response))

        dashscope.api_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
        with self._lock:
            self.close()
            options = {"model": self._model, "callback": Callback()}
            if self._endpoint:
                options["url"] = self._endpoint
            self._conversation = OmniRealtimeConversation(**options)
            self._conversation.connect()
            session_options = {
                "output_modalities": self._modalities(),
                "voice": self._voice,
                "instructions": self._instructions,
                "enable_input_audio_transcription": True,
                "enable_turn_detection": True,
                "turn_detection_type": self._vad_type,
                "turn_detection_threshold": self._vad_threshold,
                "turn_detection_silence_duration_ms": self._vad_silence_ms,
                "prefix_padding_ms": self._prefix_padding_ms,
                "input_audio_config": AudioFormatConfig(
                    type="pcm",
                    sample_rate=self._input_sample_rate,
                ),
                "output_audio_config": AudioFormatConfig(
                    type="pcm",
                    sample_rate=self._output_sample_rate,
                ),
                "tools": self._tools,
                "enable_search": False,
            }
            self._call_supported(self._conversation.update_session, session_options)

    @staticmethod
    def _modalities():
        try:
            from dashscope.audio.qwen_omni import MultiModality

            return [MultiModality.AUDIO, MultiModality.TEXT]
        except Exception:
            return ["audio", "text"]

    def append_audio(self, pcm: bytes) -> None:
        with self._lock:
            if self._conversation is not None:
                self._conversation.append_audio(base64.b64encode(pcm).decode("ascii"))

    def create_item(self, item: dict[str, Any]) -> None:
        with self._lock:
            if self._conversation is None:
                raise RuntimeError("Realtime session is not connected")
            self._conversation.create_item(item)

    def send_tool_result(self, call_id: str, result: dict[str, Any]) -> None:
        self.create_item(
            {
                "type": "function_call_output",
                "call_id": call_id,
                "output": json.dumps(result, ensure_ascii=False),
            }
        )

    def send_text(self, text: str) -> None:
        self.create_item(
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": text}],
            }
        )
        self.create_response()

    def create_response(self) -> None:
        with self._lock:
            if self._conversation is None:
                raise RuntimeError("Realtime session is not connected")
            self._call_supported(
                self._conversation.create_response,
                {"output_modalities": self._modalities()},
            )

    def cancel_response(self) -> None:
        with self._lock:
            if self._conversation is not None:
                if hasattr(self._conversation, "cancel_response"):
                    self._conversation.cancel_response()
                elif hasattr(self._conversation, "response_cancel"):
                    self._conversation.response_cancel()

    def clear_input(self) -> None:
        with self._lock:
            if self._conversation is None:
                return
            if hasattr(self._conversation, "clear_appended_audio"):
                self._conversation.clear_appended_audio()
            elif hasattr(self._conversation, "clear_audio"):
                self._conversation.clear_audio()

    def close(self) -> None:
        conversation = self._conversation
        self._conversation = None
        if conversation is None:
            return
        try:
            if hasattr(conversation, "close"):
                conversation.close()
            elif hasattr(conversation, "finish"):
                conversation.finish()
        except Exception:
            pass

    @staticmethod
    def _call_supported(function, options: dict[str, Any]):
        parameters = inspect.signature(function).parameters
        accepts_kwargs = any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )
        selected = options if accepts_kwargs else {
            key: value for key, value in options.items() if key in parameters
        }
        return function(**selected)
