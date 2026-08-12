from project_link_voice.volc_s2s_session import (
    can_continue_session,
    endpoint_event_belongs_to_turn,
    input_event_belongs_to_turn,
    no_speech_outcome,
    response_audio_belongs_to_turn,
    response_event_belongs_to_turn,
)


def test_session_greeting_events_before_user_pcm_are_rejected():
    assert not input_event_belongs_to_turn(None, 200)
    assert not input_event_belongs_to_turn(300, 299)
    assert input_event_belongs_to_turn(300, 300)


def test_audio_requires_response_created_for_current_turn():
    assert not response_audio_belongs_to_turn(None, 200, 300)
    assert not response_audio_belongs_to_turn(100, None, 300)
    assert not response_audio_belongs_to_turn(100, 400, 399)
    assert response_audio_belongs_to_turn(100, 400, 400)


def test_response_created_requires_cloud_endpoint_for_current_turn():
    assert not response_event_belongs_to_turn(None, True, 300)
    assert not response_event_belongs_to_turn(100, False, 300)
    assert not response_event_belongs_to_turn(400, True, 399)
    assert response_event_belongs_to_turn(100, True, 300)


def test_endpoint_requires_current_turn_speech_started():
    assert not endpoint_event_belongs_to_turn(None, 200, 300)
    assert not endpoint_event_belongs_to_turn(100, None, 300)
    assert not endpoint_event_belongs_to_turn(100, 400, 399)
    assert endpoint_event_belongs_to_turn(100, 200, 300)


def test_continuation_policy_and_silence_outcome():
    assert no_speech_outcome(False) == "no_speech_timeout"
    assert no_speech_outcome(True) == "continuous_silence_timeout"
    assert can_continue_session(True, 1, 8, True, True) == (True, "continue")
    assert can_continue_session(False, 1, 8, True, True) == (
        False,
        "continuous_disabled",
    )
    assert can_continue_session(True, 8, 8, True, True) == (False, "max_turns")
    assert can_continue_session(True, 1, 8, False, True) == (False, "connection_lost")
