from project_link_console_agent.voice_state import active_voice_backend, parse_voice_status


def test_active_voice_backend_uses_real_unit_state():
    assert active_voice_backend(False, False) == "off"
    assert active_voice_backend(True, False) == "classic"
    assert active_voice_backend(False, True) == "qwen_realtime"
    assert active_voice_backend(True, True) == "qwen_realtime"


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
