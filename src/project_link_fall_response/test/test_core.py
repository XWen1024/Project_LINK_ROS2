import json
from urllib import request as urllib_request

import pytest

from project_link_fall_response.core import (
    FallAssessmentError,
    FeishuBotClient,
    feishu_signature,
    image_message_content,
    parse_fall_assessment_json,
    redact_secrets,
)


def test_parse_valid_fall_assessment():
    assessment = parse_fall_assessment_json(
        json.dumps({"fall_suspected": True, "confidence": 0.82, "reason": "person lying on floor"})
    )
    assert assessment.fall_suspected is True
    assert assessment.confidence == pytest.approx(0.82)
    assert assessment.reason == "person lying on floor"


@pytest.mark.parametrize(
    "content",
    [
        "fall_suspected=true",
        "[]",
        json.dumps({"confidence": 0.8, "reason": "x"}),
        json.dumps({"fall_suspected": True, "confidence": 1.2, "reason": "x"}),
        json.dumps({"fall_suspected": True, "confidence": 0.8}),
    ],
)
def test_parse_rejects_untrusted_contracts(content):
    with pytest.raises(FallAssessmentError):
        parse_fall_assessment_json(content)


def test_image_message_content_uses_base64_data_url():
    content = image_message_content(b"\xff\xd8fake", "check")
    assert content[0] == {"type": "text", "text": "check"}
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_feishu_client_fails_closed_when_env_missing():
    result = FeishuBotClient(env={}).send_fall_alert("alert", 0.8, "reason")
    assert result.attempted is False
    assert result.success is False
    assert "FEISHU_BOT_WEBHOOK" in result.message


def test_redact_secrets_removes_values():
    assert redact_secrets("token abc webhook 123", ["abc", "123"]) == "token *** webhook ***"


def test_feishu_signature_is_stable():
    assert feishu_signature("1700000000", "secret") == feishu_signature("1700000000", "secret")


def test_feishu_client_builds_signed_text_payload(monkeypatch):
    calls = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return json.dumps({"StatusCode": 0, "StatusMessage": "success"}).encode("utf-8")

    def fake_urlopen(req, timeout):
        calls["url"] = req.full_url
        calls["timeout"] = timeout
        calls["payload"] = json.loads(req.data.decode("utf-8"))
        return Response()

    monkeypatch.setattr(urllib_request, "urlopen", fake_urlopen)
    monkeypatch.setattr("project_link_fall_response.core.time.time", lambda: 1700000000)

    env = {
        "FEISHU_BOT_WEBHOOK": "https://open.feishu.cn/open-apis/bot/v2/hook/token",
        "FEISHU_BOT_SECRET": "secret",
        "FEISHU_BOT_TIMEOUT_SEC": "3",
    }
    result = FeishuBotClient(env=env).send_fall_alert("alert-1", 0.83, "person lying down")
    assert result.attempted is True
    assert result.success is True
    assert calls["timeout"] == 3
    assert calls["payload"]["msg_type"] == "text"
    assert calls["payload"]["timestamp"] == "1700000000"
    assert calls["payload"]["sign"] == feishu_signature("1700000000", "secret")
    assert "alert-1" in calls["payload"]["content"]["text"]
