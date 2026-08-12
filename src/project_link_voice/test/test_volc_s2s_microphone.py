import threading
import types

from project_link_voice.volc_s2s_microphone import RawPcmCaptureSettings, ServerVadPcmRecorder


def test_cloud_endpoint_has_priority_over_local_safety_timeouts():
    recorder = ServerVadPcmRecorder(RawPcmCaptureSettings())
    endpoint = threading.Event()
    endpoint.set()
    assert recorder._stop_reason(endpoint, None, 0, 100_000_000_000) == "server_vad_endpoint"


def test_no_speech_timeout_is_only_a_hard_capture_guard():
    recorder = ServerVadPcmRecorder(RawPcmCaptureSettings(no_speech_timeout_sec=8.0))
    endpoint = threading.Event()
    assert recorder._stop_reason(endpoint, None, 0, 7_999_999_999) is None
    assert recorder._stop_reason(endpoint, None, 0, 8_000_000_000) == "no_speech_timeout"


def test_max_utterance_guard_starts_from_cloud_speech_started():
    recorder = ServerVadPcmRecorder(RawPcmCaptureSettings(max_utterance_sec=30.0))
    endpoint = threading.Event()
    speech_started_ns = 5_000_000_000
    assert recorder._stop_reason(endpoint, speech_started_ns, 0, 34_999_999_999) is None
    assert (
        recorder._stop_reason(endpoint, speech_started_ns, 0, 35_000_000_000)
        == "max_utterance_timeout"
    )


def test_chunk_read_after_cloud_endpoint_is_not_uploaded(monkeypatch):
    endpoint = threading.Event()

    class FakeStream:
        def read(self, _frames, exception_on_overflow=False):
            assert exception_on_overflow is False
            endpoint.set()
            return b"late-pcm"

        def stop_stream(self):
            pass

        def close(self):
            pass

    class FakeAudio:
        def get_device_count(self):
            return 1

        def get_device_info_by_index(self, _index):
            return {"name": "XFM-DP-V0.0.18", "maxInputChannels": 1}

        def open(self, **_kwargs):
            return FakeStream()

        def terminate(self):
            pass

    fake_pyaudio = types.SimpleNamespace(paInt16=8, PyAudio=FakeAudio)
    monkeypatch.setitem(__import__("sys").modules, "pyaudio", fake_pyaudio)
    uploaded = []
    recorder = ServerVadPcmRecorder(
        RawPcmCaptureSettings(),
        input_device_name="XFM-DP-V0.0.18",
    )
    sent_bytes, reason = recorder.record(endpoint, lambda: None, uploaded.append)
    assert (sent_bytes, reason, uploaded) == (0, "server_vad_endpoint", [])
