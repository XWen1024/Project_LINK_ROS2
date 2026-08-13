import sys
import threading
from types import SimpleNamespace

from project_link_voice.llm import StreamingTextEmitter, TOOL_SCHEMAS, ToolCallingClient, ToolResult


def test_llm_disabled_returns_clear_message(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("SILICONFLOW_API_KEY", "legacy-key-is-not-the-default")
    client = ToolCallingClient(True, "https://example.invalid/v1", "test-model")
    result = client.chat("去客厅", lambda _name, _args: ToolResult({"success": True}))
    assert result.kind == "text"
    assert "DEEPSEEK_API_KEY" in result.reply


def test_llm_api_key_environment_can_be_overridden(monkeypatch):
    monkeypatch.setenv("CUSTOM_LLM_API_KEY", "test-key")
    client = ToolCallingClient(
        True,
        "https://example.invalid/v1",
        "test-model",
        api_key_env="CUSTOM_LLM_API_KEY",
    )
    assert client.available() == (True, "ready")


def test_audio_conversation_can_reset_llm_history():
    client = ToolCallingClient(False, "https://example.invalid/v1", "test-model")
    client.append_system_event("old session")
    assert client._history

    client.reset_history()
    assert client._history == []


def test_tool_result_can_stop_before_ros_execution():
    handled = ToolResult(
        {"success": True, "pending": "navigation", "target_name": "客厅"},
        stop_after_tool=True,
        spoken_reply="准备前往客厅，请确认开始。",
    )
    assert handled.stop_after_tool
    assert handled.spoken_reply == "准备前往客厅，请确认开始。"


def test_navigation_tool_descriptions_are_backend_neutral():
    descriptions = {
        schema["function"]["name"]: schema["function"]["description"]
        for schema in TOOL_SCHEMAS
    }
    assert "navigation" in descriptions["navigate_to_location"]
    assert "direct-drive" not in descriptions["navigate_to_location"]


def test_parse_bad_tool_args_is_empty_dict():
    assert ToolCallingClient._parse_args("{bad json") == {}
    assert ToolCallingClient._parse_args("[]") == {}


def test_streaming_text_emitter_flushes_on_strict_timer():
    emitted = []
    completed = threading.Event()

    def callback(text):
        emitted.append(text)
        completed.set()

    emitter = StreamingTextEmitter(callback, max_delay_sec=0.01, max_chars=99)
    emitter.feed("好")

    assert completed.wait(0.2)
    assert emitted == ["好"]


def test_llm_timing_callback_reports_api_parse_tool_and_total(monkeypatch):
    tool_delta = SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            index=0,
                            id="call-1",
                            function=SimpleNamespace(name="list_saved_locations", arguments="{}"),
                        )
                    ],
                )
            )
        ]
    )
    text_delta = SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content="完成", tool_calls=None))]
    )
    streams = iter([[tool_delta], [text_delta]])
    requests = []

    def create(**kwargs):
        requests.append(kwargs)
        return iter(next(streams))

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=create)
        )
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setitem(
        sys.modules,
        "openai",
        SimpleNamespace(OpenAI=lambda **_kwargs: fake_client),
    )
    timings = []
    client = ToolCallingClient(True, "https://example.invalid", "test-model")

    result = client.chat(
        "列出地点",
        lambda _name, _args: ToolResult({"success": True, "locations": []}),
        timing_callback=lambda phase, elapsed_ms, fields: timings.append((phase, elapsed_ms, fields)),
    )

    phases = [phase for phase, _elapsed_ms, _fields in timings]
    assert result.reply == "完成"
    assert phases.count("llm_api_roundtrip") == 2
    assert "llm_tool_arguments_parse" in phases
    assert "python_tool" in phases
    assert "llm_first_text" in phases
    assert phases[-1] == "llm_total"
    assert all(request["extra_body"] == {"thinking": {"type": "disabled"}} for request in requests)
    assert all(request["max_tokens"] == 384 for request in requests)


def test_tool_call_cancels_any_started_streamed_text(monkeypatch):
    text_delta = SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content="马上", tool_calls=None))]
    )
    tool_delta = SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            index=0,
                            id="call-1",
                            function=SimpleNamespace(name="list_saved_locations", arguments="{}"),
                        )
                    ],
                )
            )
        ]
    )
    final_delta = SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content="完成", tool_calls=None))]
    )
    streams = iter([[text_delta, tool_delta], [final_delta]])
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **_kwargs: iter(next(streams)))
        )
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=lambda **_kwargs: fake_client))
    emitted = []
    canceled = []
    client = ToolCallingClient(True, "https://example.invalid", "test-model")

    result = client.chat(
        "列出地点",
        lambda _name, _args: ToolResult({"success": True, "locations": []}),
        text_callback=lambda value: emitted.append(value),
        text_cancel_callback=lambda: canceled.append(True),
    )

    assert result.reply == "完成"
    assert canceled == [True]
    assert "马上" not in emitted
