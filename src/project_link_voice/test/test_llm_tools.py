from project_link_voice.llm import ToolCallingClient, ToolResult


def test_llm_disabled_returns_clear_message(monkeypatch):
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    client = ToolCallingClient(True, "https://example.invalid/v1", "test-model")
    result = client.chat("去客厅", lambda _name, _args: ToolResult({"success": True}))
    assert result.kind == "text"
    assert "SILICONFLOW_API_KEY" in result.reply


def test_tool_result_can_stop_before_ros_execution():
    handled = ToolResult(
        {"success": True, "pending": "navigation", "target_name": "客厅"},
        stop_after_tool=True,
        spoken_reply="准备前往客厅，请确认开始。",
    )
    assert handled.stop_after_tool
    assert handled.spoken_reply == "准备前往客厅，请确认开始。"


def test_parse_bad_tool_args_is_empty_dict():
    assert ToolCallingClient._parse_args("{bad json") == {}
    assert ToolCallingClient._parse_args("[]") == {}
