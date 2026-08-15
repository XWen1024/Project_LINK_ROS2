from project_link_console_agent.voice_state import parse_voice_status


def test_classic_voice_status_is_normalized():
    state = parse_voice_status("awaiting_confirmation_navigate; mode=production")
    assert state.backend == "classic"
    assert state.pending_task == "navigate"
    assert not state.idle


def test_qwen_voice_status_is_normalized():
    state = parse_voice_status(
        '{"backend":"qwen_realtime","conversation_active":false,"pending_task":"","active_task":""}'
    )
    assert state.backend == "qwen_realtime"
    assert state.idle
