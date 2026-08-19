from project_link_voice.wakeup import (
    DEFAULT_WAKEUP_ALIAS,
    IFLYTEK_WAKE_BY_ID,
    SerialWakeDetector,
    resolve_wakeup_serial_port,
)


def test_serial_wake_detector_matches_across_fragment_boundaries():
    detector = SerialWakeDetector("aiui_event")

    assert detector.feed(b'noise {"type":"aiui_') is None
    matched = detector.feed(b'event","content":{}} tail')

    assert matched is not None
    assert "aiui_event" in matched


def test_serial_wake_detector_trims_noise_and_keeps_matching():
    detector = SerialWakeDetector("wake", max_buffer_bytes=16)

    assert detector.feed(b"x" * 64 + b"wa") is None
    assert detector.feed(b"ke") is not None


def test_serial_wake_detector_accepts_aiui_key_case_variants():
    detector = SerialWakeDetector("aiui_event")
    assert detector.feed(b'{"type":"AIUI_EVENT"}') is not None


def test_empty_match_accepts_first_nonempty_chunk():
    detector = SerialWakeDetector("")

    assert detector.feed(b"hello") == "hello"


def test_missing_alias_falls_back_to_stable_by_id(monkeypatch):
    monkeypatch.setattr(
        "project_link_voice.wakeup.os.path.exists",
        lambda path: path == IFLYTEK_WAKE_BY_ID,
    )
    assert resolve_wakeup_serial_port(DEFAULT_WAKEUP_ALIAS) == IFLYTEK_WAKE_BY_ID
