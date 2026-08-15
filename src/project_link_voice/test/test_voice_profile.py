import json

from project_link_voice.voice_profile import configured_tool_schemas, prompt_for


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "old",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_current_task",
            "description": "cancel",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def test_missing_profile_preserves_built_in_tools(tmp_path):
    assert configured_tool_schemas(SCHEMAS, str(tmp_path / "missing.json")) == SCHEMAS


def test_profile_can_edit_and_remove_registered_tools(tmp_path):
    path = tmp_path / "voice_profile.json"
    path.write_text(
        json.dumps(
            {
                "prompts": {"classic": "自定义 {current_time}"},
                "tools": [
                    {
                        "name": "get_weather",
                        "enabled": True,
                        "description": "新天气说明",
                        "parameters": {
                            "type": "object",
                            "properties": {"city_name": {"type": "string"}},
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    configured = configured_tool_schemas(SCHEMAS, str(path))
    assert [item["function"]["name"] for item in configured] == ["get_weather"]
    assert configured[0]["function"]["description"] == "新天气说明"
    assert prompt_for("classic", "fallback", str(path)) == "自定义 {current_time}"
