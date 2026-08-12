import numpy as np

from project_link_voice.funvad import (
    FunVadRecorder,
    VadEndpointState,
    VadSettings,
    extract_vad_events,
    resolve_input_device_index,
)


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


def test_recorder_passes_normalized_waveform_and_streaming_chunk_size():
    calls = []

    class FakeModel:
        def generate(self, **kwargs):
            calls.append(kwargs)
            return [{"value": [[0, -1]]}]

    recorder = FunVadRecorder(VadSettings(chunk_ms=200), "test-model", "cpu")
    pcm = np.array([-32768, 0, 16384, 32767], dtype=np.int16).tobytes()

    events = recorder._generate_events(FakeModel(), pcm, {}, is_final=False)

    waveform = calls[0]["input"]
    assert waveform.dtype == np.float32
    assert np.allclose(waveform, [-1.0, 0.0, 0.5, 32767 / 32768.0])
    assert calls[0]["chunk_size"] == 200
    assert calls[0]["is_final"] is False
    assert events == [(0, -1)]


def test_resolve_input_device_by_name_ignores_changed_numeric_index():
    class FakeAudio:
        devices = [
            {"name": "pulse", "maxInputChannels": 32},
            {"name": "XFM-DP-V0.0.18: USB Audio (hw:3,0)", "maxInputChannels": 1},
        ]

        def get_device_count(self):
            return len(self.devices)

        def get_device_info_by_index(self, index):
            return self.devices[index]

    assert resolve_input_device_index(FakeAudio(), "XFM-DP-V0.0.18", 99) == (
        1,
        "XFM-DP-V0.0.18: USB Audio (hw:3,0)",
    )


def test_resolve_input_device_rejects_output_only_index():
    class FakeAudio:
        def get_device_count(self):
            return 1

        def get_device_info_by_index(self, index):
            return {"name": "USB speaker", "maxInputChannels": 0}

    try:
        resolve_input_device_index(FakeAudio(), "", 0)
    except RuntimeError as exc:
        assert "no input channels" in str(exc)
    else:
        raise AssertionError("output-only index should be rejected")
