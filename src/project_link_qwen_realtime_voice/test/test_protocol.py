from types import SimpleNamespace

from project_link_qwen_realtime_voice.protocol import normalize_event


def test_normalize_dict_event():
    event = normalize_event({"type": "response.audio.delta", "delta": "abc"})
    assert event.type == "response.audio.delta"
    assert event.payload["delta"] == "abc"


def test_normalize_sdk_output_object():
    event = normalize_event(SimpleNamespace(output={"type": "session.updated"}))
    assert event.type == "session.updated"
