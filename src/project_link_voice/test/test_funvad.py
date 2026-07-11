from project_link_voice.funvad import VadEndpointState, VadSettings, extract_vad_events


def test_vad_end_keeps_preroll_and_finishes():
    state = VadEndpointState(VadSettings(chunk_ms=100, pre_roll_ms=200, min_speech_sec=0.2))
    state.feed(b"a", [])
    state.feed(b"b", [(0, -1)])
    assert state.feed(b"c", [(-1, 300)]) == "vad_end"
    assert state.audio == b"abc"


def test_no_speech_timeout_is_bounded():
    state = VadEndpointState(VadSettings(chunk_ms=100, no_speech_timeout_sec=0.3))
    assert state.feed(b"a", []) is None
    assert state.feed(b"b", []) is None
    assert state.feed(b"c", []) == "no_speech_timeout"


def test_max_utterance_forces_end_when_noise_never_ends():
    state = VadEndpointState(VadSettings(chunk_ms=100, min_speech_sec=0.1, max_utterance_sec=0.3))
    assert state.feed(b"a", [(0, -1)]) is None
    assert state.feed(b"b", []) is None
    assert state.feed(b"c", []) == "max_utterance_timeout"


def test_extract_events_from_funasr_style_result():
    events = extract_vad_events([{"key": "x", "value": [[-1, -1], [0, -1], [-1, 320]]}])
    assert events == [(-1, -1), (0, -1), (-1, 320)]