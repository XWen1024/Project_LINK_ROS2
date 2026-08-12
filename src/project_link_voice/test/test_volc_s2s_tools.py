from project_link_voice.volc_s2s_tools import (
    FunctionCall,
    build_followup_response_event,
    build_function_output_event,
    execute_safe_function,
    function_call_from_arguments_done,
    function_call_from_item,
    function_calls_from_legacy_array,
)


def test_parse_official_function_call_events():
    pending = function_call_from_item(
        {"type": "function_call", "call_id": "call-1", "name": "get_magic_number", "arguments": ""}
    )
    assert pending == FunctionCall("call-1", "get_magic_number", "{}")
    assert function_call_from_arguments_done(
        {"type": "response.function_call_arguments.done", "arguments": "{}"},
        pending,
    ) == FunctionCall("call-1", "get_magic_number", "{}")


def test_parse_legacy_tool_calls_array():
    assert function_calls_from_legacy_array(
        {"tool_calls": [{"id": "call-2", "function": {"name": "get_magic_number", "arguments": {}}}]}
    ) == [FunctionCall("call-2", "get_magic_number", "{}")]


def test_safe_tool_is_fail_closed():
    assert execute_safe_function(FunctionCall("x", "get_magic_number", "{}")) == {"number": 42}
    assert execute_safe_function(FunctionCall("x", "move_robot", "{}")) == {
        "error": "unsupported_function",
        "function": "move_robot",
    }


def test_function_output_and_followup_schema_matches_smoke_test():
    assert build_function_output_event("call-3", {"number": 42}) == {
        "type": "conversation.item.create",
        "item": {
            "call_id": "call-3",
            "type": "function_call_output",
            "object": "realtime.item",
            "output": '{"number":42}',
        },
    }
    assert build_followup_response_event() == {
        "type": "response.create",
        "response": {"modalities": ["text", "audio"]},
    }
