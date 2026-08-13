import json
import queue
import threading
from collections import OrderedDict

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
    asr_row = next(row for row in timing_rows if row.get("phase") == "asr")
    assert asr_row["phase_elapsed_ms"] == 12.346
    assert asr_row["step_delta_ms"] >= 0.0
    assert asr_row["trace_total_ms"] >= asr_row["step_delta_ms"]
    assert any(row.get("late_tts_update") is True for row in timing_rows)
    assert all(row["trace_id"] == trace.trace_id for row in debug_rows + timing_rows)
    assert any(" phase=asr " in message and " phase_elapsed=12.346ms" in message for message in logger.info_messages)
    assert any(message.startswith("[VOICE_TIMING] 20") and " +" in message for message in logger.info_messages)


def test_late_playback_update_rewrites_summary_with_acoustic_metrics(tmp_path):
    timing_path = tmp_path / "voice_timing.jsonl"
    sink = VoiceDebugSink(
        FakeLogger(),
        debug_enabled=False,
        timing_log_file=str(timing_path),
    )
    trace = sink.start_trace("audio")
    trace.mark_reference("speech_end_estimated")
    trace.complete("reply_dispatched")
    trace.timing_callback("tts_first_audio", 20.0, {})
    trace.timing_callback("tts_playback_started", 25.0, {})

    rows = [json.loads(line) for line in timing_path.read_text(encoding="utf-8").splitlines()]
    summaries = [row for row in rows if row.get("kind") == "timing_summary"]
    assert summaries[-1]["late_tts_update"] is True
    assert "speech_end_to_first_playback" in summaries[-1]["phases_ms"]


def test_derived_metrics_do_not_advance_timeline_delta(tmp_path):
    timing_path = tmp_path / "voice_timing.jsonl"
    sink = VoiceDebugSink(FakeLogger(), debug_enabled=False, timing_log_file=str(timing_path))
    trace = sink.start_trace("audio")
    trace.mark_reference("vad_terminal")
    trace.record("asr_final", 10.0)
    trace.record("llm_request_sent", 0.0)

    rows = [json.loads(line) for line in timing_path.read_text(encoding="utf-8").splitlines()]
    derived_rows = [row for row in rows if row.get("derived")]
    request_row = next(row for row in rows if row.get("phase") == "llm_request_sent")
    assert derived_rows
    assert all(row["step_delta_ms"] == 0.0 for row in derived_rows)
    assert request_row["step_delta_ms"] >= 0.0


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
        "tts_request_sent",
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
    tts._phrase_cache = OrderedDict()
    tts._cache_candidates = OrderedDict()
    tts._persistent_files = {}
    tts._waiting_prompt_active = threading.Event()
    tts._generation_lock = threading.Lock()
    tts._playback_generation = 0
    tts._active_stream_generation = 0
    tts._shutdown_flag = threading.Event()
    tts._cache_bytes = 0
    tts._cache_lock = threading.Lock()
    tts._cache_ttl_sec = 900.0
    tts._cache_max_entries = 64
    tts._cache_max_bytes = 16 * 1024 * 1024
    tts._audio_queue = queue.Queue()
    tts._cmd_queue = queue.Queue()
    tts._mixer_ready = False

    tts.speak("这是一条超过二十五个字符但在调用前已经完整确定的现场语音播报文本。")

    assert tts._cmd_queue.get_nowait()["type"] == "stop"
    command = tts._cmd_queue.get_nowait()
    assert command["type"] == "full_text"
    assert command["generation"] == tts._playback_generation
    assert tts._cmd_queue.empty()


def test_dynamic_tts_cache_reuses_first_synthesis_on_second_request():
    tts = VolcanoTts.__new__(VolcanoTts)
    tts._phrase_cache = OrderedDict()
    tts._cache_candidates = OrderedDict()
    tts._cache_bytes = 0
    tts._cache_lock = threading.Lock()
    tts._cache_ttl_sec = 900.0
    tts._cache_max_entries = 4
    tts._cache_max_bytes = 1024

    tts._cache_admit(" 好的。 ", b"one")
    assert tts._cache_get("好的。") == b"one"
    assert tts._cache_get("好的。") == b"one"


def test_old_waiting_prompt_cannot_mark_a_new_tts_generation_idle():
    tts = VolcanoTts.__new__(VolcanoTts)
    tts._generation_lock = threading.Lock()
    tts._playback_generation = 2
    tts._is_playing = True

    tts._mark_idle_if_current(1)
    assert tts._is_playing

    tts._mark_idle_if_current(2)
    assert not tts._is_playing
