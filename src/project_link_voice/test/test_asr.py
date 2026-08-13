import gzip
import json
import struct

from project_link_voice.asr import (
    VolcanoAsrSettings,
    build_audio_request,
    build_full_request,
    create_asr_provider,
    extract_transcript,
    parse_response,
)


def test_full_request_uses_gzip_json_and_positive_sequence():
    request = build_full_request(7, {"request": {"model_name": "bigmodel"}})
    assert request[:4] == bytes([0x11, 0x11, 0x11, 0x00])
    assert struct.unpack(">i", request[4:8])[0] == 7
    size = struct.unpack(">I", request[8:12])[0]
    assert json.loads(gzip.decompress(request[12 : 12 + size]))["request"]["model_name"] == "bigmodel"


def test_last_audio_request_uses_negative_sequence():
    request = build_audio_request(9, b"pcm", is_last=True)
    assert request[1] & 0x0F == 0b0011
    assert struct.unpack(">i", request[4:8])[0] == -9
    size = struct.unpack(">I", request[8:12])[0]
    assert gzip.decompress(request[12 : 12 + size]) == b"pcm"


def test_parse_last_server_response():
    payload = gzip.compress(json.dumps({"result": {"text": "前进一点"}}).encode())
    response = parse_response(
        bytes([0x11, 0x93, 0x11, 0x00])
        + struct.pack(">iI", -4, len(payload))
        + payload
    )
    assert response.is_last_package
    assert response.sequence == -4
    assert response.payload["result"]["text"] == "前进一点"


def test_extract_transcript_prefers_full_result_over_nested_words():
    text, definite = extract_transcript(
        {
            "result": {
                "text": "去客厅",
                "utterances": [
                    {"text": "去客厅", "definite": True, "words": [{"text": "客厅"}]}
                ],
            }
        }
    )
    assert text == "去客厅"
    assert definite is True


def test_provider_switch_is_explicit(monkeypatch):
    local = create_asr_provider("faster_whisper", "small", "cpu", "int8", VolcanoAsrSettings())
    assert local.name == "faster_whisper"
    monkeypatch.setenv("VOLCANO_ASR_API_KEY", "test")
    cloud = create_asr_provider("volcano", "small", "cpu", "int8", VolcanoAsrSettings())
    assert cloud.name == "volcano"


def test_volcano_asr_buffer_is_bounded_but_tolerates_short_network_jitter():
    settings = VolcanoAsrSettings()
    assert settings.max_buffer_sec == 4.0
