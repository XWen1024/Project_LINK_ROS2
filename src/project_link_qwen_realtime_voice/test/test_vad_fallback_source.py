from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_qwen_node_has_local_audio_commit_fallback_and_reconnect_gate():
    source = (
        ROOT
        / "src/project_link_qwen_realtime_voice/project_link_qwen_realtime_voice/node.py"
    ).read_text(encoding="utf-8")
    assert "or not self._session_ready.is_set()" in source
    assert "local_audio_fallback_commit" in source
    assert "self._transport.commit_audio()" in source
    assert "microphone_voiced_chunks" in source
