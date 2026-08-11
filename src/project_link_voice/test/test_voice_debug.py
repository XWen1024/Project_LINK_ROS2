import json
import queue
import threading

from project_link_voice.voice_debug import VoiceDebugSink
from project_link_voice.volcano_tts import VolcanoTts


class FakeLogger:
    def __init__(self):
        self.debug_messages = []
        self.info_messages = []
        self.warning_messages = []

    def debug(self, message):
        self.debug_messages.append(message)

    def info(self, message):
        self.info_messages.append(message)

    def warning(self, message):
        self.warning_messages.append(message)


def test_voice_trace_writes_debug_timing_and_summary(tmp_path):
    debug_path = tmp_path / "voice_debug.jsonl"
    timing_path = tmp_path / "voice_timing.jsonl"
    logger = FakeLogger()
    sink = VoiceDebugSink(
        logger,
        debug_log_file=str(debug_path),
        timing_log_file=str(timing_path),
    )

    trace = sink.start_trace("text_topic", text_chars=4)
    trace.debug("asr_result", text_preview="向前走")
    trace.record("asr", 12.3456, success=True)
    trace.complete("text_reply")
    trace.record("tts_synthesis_complete", 45.6, success=True)

    debug_rows = [json.loads(line) for line in debug_path.read_text(encoding="utf-8").splitlines()]
    timing_rows = [json.loads(line) for line in timing_path.read_text(encoding="utf-8").splitlines()]

    assert {row["event"] for row in debug_rows} == {"trace_started", "asr_result", "trace_completed"}
    assert any(row.get("phase") == "asr" and row["elapsed_ms"] == 12.346 for row in timing_rows)
    assert any(row.get("late_tts_update") is True for row in timing_rows)
    assert all(row["trace_id"] == trace.trace_id for row in debug_rows + timing_rows)
    assert logger.info_messages


def test_disabled_voice_logs_do_not_create_files(tmp_path):
    debug_path = tmp_path / "voice_debug.jsonl"
    timing_path = tmp_path / "voice_timing.jsonl"
    sink = VoiceDebugSink(
        FakeLogger(),
        debug_enabled=False,
        timing_enabled=False,
        debug_log_file=str(debug_path),
        timing_log_file=str(timing_path),
    )

    trace = sink.start_trace("text_topic")
    trace.record("asr", 1.0)
    trace.complete("done")

    assert not debug_path.exists()
    assert not timing_path.exists()


def test_mock_tts_reports_first_audio_and_completion():
    timings = []
    tts = VolcanoTts(enabled=False)

    tts.speak(
        "测试",
        lambda phase, elapsed_ms, fields: timings.append((phase, elapsed_ms, fields)),
    )

    assert [phase for phase, _elapsed_ms, _fields in timings] == [
        "tts_first_audio",
        "tts_synthesis_complete",
    ]
    assert all(fields["mock"] for _phase, _elapsed_ms, fields in timings)


def test_complete_long_text_uses_one_full_text_command():
    tts = VolcanoTts.__new__(VolcanoTts)
    tts._mock_mode = False
    tts._play_lock = threading.Lock()
    tts._stop_flag = threading.Event()
    tts._is_playing = False
    tts._phrase_cache = {}
    tts._audio_queue = queue.Queue()
    tts._cmd_queue = queue.Queue()
    tts._mixer_ready = False

    tts.speak("这是一条超过二十五个字符但在调用前已经完整确定的现场语音播报文本。")

    command = tts._cmd_queue.get_nowait()
    assert command["type"] == "full_text"
    assert tts._cmd_queue.empty()
