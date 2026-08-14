import json

import pytest

from project_link_qwen_realtime_voice.weather import normalize_api_host, query_current_weather


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_normalize_api_host_requires_project_domain_only():
    assert normalize_api_host("demo.re.qweatherapi.com/") == "https://demo.re.qweatherapi.com"
    with pytest.raises(ValueError):
        normalize_api_host("")
    with pytest.raises(ValueError):
        normalize_api_host("https://demo.re.qweatherapi.com/v7/weather/now")


def test_weather_uses_project_host_header_and_new_paths():
    requests = []
    responses = iter([
        {"code": "200", "location": [{"id": "101280601", "name": "深圳"}]},
        {"code": "200", "now": {"text": "晴", "temp": "30", "feelsLike": "32", "humidity": "60"}},
    ])

    def opener(request, timeout):
        requests.append((request, timeout))
        return FakeResponse(next(responses))

    result = query_current_weather(
        "深圳",
        "test-key",
        "demo.re.qweatherapi.com",
        opener=opener,
    )
    assert result["success"] is True
    assert result["text"] == "晴"
    assert requests[0][0].full_url.startswith("https://demo.re.qweatherapi.com/geo/v2/city/lookup?")
    assert requests[1][0].full_url.startswith("https://demo.re.qweatherapi.com/v7/weather/now?")
    assert requests[0][0].get_header("X-qw-api-key") == "test-key"
