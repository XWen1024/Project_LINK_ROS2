from project_link_qwen_realtime_voice.audio import DuplexPcmAudio, pcm16_levels, resolve_device


def test_pcm16_levels_reports_peak_and_rms():
    peak, rms = pcm16_levels(b"\x00\x00\xe8\x03\x18\xfc")
    assert peak == 1000
    assert 816.0 < rms < 817.0


class FakePortAudio:
    def __init__(self):
        self.devices = [
            {"name": "C-Media USB Audio", "maxInputChannels": 0, "maxOutputChannels": 2},
            {"name": "XFM-DP-V0.0.18 USB Audio", "maxInputChannels": 1, "maxOutputChannels": 0},
        ]

    def get_device_count(self):
        return len(self.devices)

    def get_device_info_by_index(self, index):
        return self.devices[index]


def test_resolve_stable_microphone_name():
    assert resolve_device(FakePortAudio(), "XFM-DP-V0.0.18", True) == 1
    assert resolve_device(FakePortAudio(), "C-Media", False) == 0


def test_audio_generation_rejects_stale_chunks():
    audio = DuplexPcmAudio(lambda _pcm: None, "", "")
    generation = audio.next_generation()
    assert audio.enqueue(b"\x00\x00", generation)
    audio.interrupt()
    assert not audio.enqueue(b"\x00\x00", generation)
