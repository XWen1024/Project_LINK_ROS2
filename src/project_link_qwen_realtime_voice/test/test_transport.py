import base64
import sys
from types import ModuleType

from project_link_qwen_realtime_voice.transport import DashScopeRealtimeTransport


class FakeConversation:
    instance = None

    def __init__(self, **options):
        self.options = options
        self.session = None
        self.audio = []
        self.items = []
        self.responses = 0
        self.canceled = 0
        FakeConversation.instance = self

    def connect(self):
        return None

    def update_session(self, output_modalities, voice, instructions, tools, **kwargs):
        self.session = {
            "output_modalities": output_modalities,
            "voice": voice,
            "instructions": instructions,
            "tools": tools,
            **kwargs,
        }

    def append_audio(self, value):
        self.audio.append(value)

    def create_item(self, item):
        self.items.append(item)

    def create_response(self, **_kwargs):
        self.responses += 1

    def cancel_response(self):
        self.canceled += 1

    def close(self):
        return None


class FakeCallback:
    pass


def install_fake_dashscope(monkeypatch):
    dashscope = ModuleType("dashscope")
    dashscope.api_key = ""
    audio = ModuleType("dashscope.audio")
    qwen_omni = ModuleType("dashscope.audio.qwen_omni")
    qwen_omni.OmniRealtimeConversation = FakeConversation
    qwen_omni.OmniRealtimeCallback = FakeCallback
    qwen_omni.MultiModality = type("MultiModality", (), {"AUDIO": "audio", "TEXT": "text"})
    qwen_omni.AudioFormatConfig = lambda **kwargs: kwargs
    monkeypatch.setitem(sys.modules, "dashscope", dashscope)
    monkeypatch.setitem(sys.modules, "dashscope.audio", audio)
    monkeypatch.setitem(sys.modules, "dashscope.audio.qwen_omni", qwen_omni)


def test_transport_configures_semantic_vad_and_tools(monkeypatch):
    install_fake_dashscope(monkeypatch)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    transport = DashScopeRealtimeTransport(
        lambda _event: None,
        "wss://workspace.example/realtime",
        "qwen3.5-omni-flash-realtime",
        "Ethan",
        "instructions",
        [{"type": "function", "function": {"name": "test"}}],
    )
    transport.connect()
    conversation = FakeConversation.instance
    assert conversation.session["turn_detection_type"] == "semantic_vad"
    assert conversation.session["turn_detection_threshold"] == 0.5
    assert conversation.session["turn_detection_silence_duration_ms"] == 800
    assert conversation.session["enable_search"] is False

    transport.append_audio(b"pcm")
    assert base64.b64decode(conversation.audio[0]) == b"pcm"
    transport.send_text("hello")
    assert conversation.items[-1]["content"][0]["text"] == "hello"
    assert conversation.responses == 1
    transport.send_tool_result("call-1", {"success": True})
    assert conversation.items[-1]["call_id"] == "call-1"
