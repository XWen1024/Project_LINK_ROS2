from project_link_voice.wakeup import SerialWakeDetector


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


def test_empty_match_accepts_first_nonempty_chunk():
    detector = SerialWakeDetector("")

    assert detector.feed(b"hello") == "hello"
