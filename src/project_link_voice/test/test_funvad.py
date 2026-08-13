import numpy as np

from project_link_voice.funvad import (
    FunVadRecorder,
    VadEndpointState,
    VadSettings,
    extract_vad_events,
    resolve_pyaudio_input_device,
    vad_settings_for_turn,
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


def test_follow_up_turn_can_use_a_shorter_silence_timeout():
    original = VadSettings(no_speech_timeout_sec=8.0)
    follow_up = vad_settings_for_turn(original, 3.5)
    assert original.no_speech_timeout_sec == 8.0
    assert follow_up.no_speech_timeout_sec == 3.5


def test_follow_up_silence_timeout_has_a_positive_floor():
    follow_up = vad_settings_for_turn(VadSettings(), 0.0)
    assert follow_up.no_speech_timeout_sec == 0.1


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
    assert calls[0]["max_end_silence_time"] == 500
    assert calls[0]["is_final"] is False
    assert events == [(0, -1)]


def test_capture_frame_is_smaller_than_vad_chunk():
    settings = VadSettings(sample_rate=16000, capture_frame_ms=20, chunk_ms=200)
    assert settings.capture_frame_bytes == 640
    assert settings.chunk_bytes == 6400
    assert settings.end_silence_ms == 500


def test_input_device_name_survives_usb_index_change():
    class FakeAudio:
        devices = [
            {"name": "C-Media USB Audio", "maxInputChannels": 0, "defaultSampleRate": 48000},
            {"name": "pulse", "maxInputChannels": 32, "defaultSampleRate": 44100},
            {
                "name": "XFM-DP-V0.0.18: USB Audio (hw:2,0)",
                "maxInputChannels": 1,
                "defaultSampleRate": 16000,
            },
        ]

        def get_device_count(self):
            return len(self.devices)

        def get_device_info_by_index(self, index):
            return self.devices[index]

    index, name = resolve_pyaudio_input_device(FakeAudio(), None, "XFM-DP-V0.0.18", 16000)
    assert index == 2
    assert "XFM-DP-V0.0.18" in name
