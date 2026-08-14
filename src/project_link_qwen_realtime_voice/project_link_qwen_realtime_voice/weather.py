"""QWeather API client using the project-specific API host."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any


def normalize_api_host(value: str) -> str:
    host = value.strip().rstrip("/")
    if not host:
        raise ValueError("天气 API 未配置 QWEATHER_API_HOST。")
    if any(character.isspace() for character in host):
        raise ValueError("QWEATHER_API_HOST 不能包含空格。")
    if "://" not in host:
        host = "https://" + host
    parsed = urllib.parse.urlsplit(host)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("QWEATHER_API_HOST 必须是有效的 HTTPS API Host。")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("QWEATHER_API_HOST 只能填写域名，不能包含 API 路径。")
    return f"https://{parsed.netloc}"


def query_current_weather(
    city: str,
    api_key: str,
    api_host: str,
    timeout_sec: float = 5.0,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    city_name = city.strip()
    key = api_key.strip()
    if not city_name:
        raise ValueError("缺少城市名称。")
    if not key:
        raise ValueError("天气 API 未配置 QWEATHER_API_KEY。")
    host = normalize_api_host(api_host)
    headers = {"X-QW-Api-Key": key, "User-Agent": "Project-LINK-Qwen-Realtime/1.0"}
    lookup = _request_json(
        host + "/geo/v2/city/lookup?" + urllib.parse.urlencode({"location": city_name}),
        headers,
        timeout_sec,
        opener,
    )
    locations = lookup.get("location") or []
    if not locations:
        raise RuntimeError(f"没有找到城市：{city_name}。")
    location = locations[0]
    weather = _request_json(
        host + "/v7/weather/now?" + urllib.parse.urlencode({"location": str(location["id"])}),
        headers,
        timeout_sec,
        opener,
    )
    now = weather.get("now") or {}
    return {
        "success": True,
        "city": location.get("name", city_name),
        "text": now.get("text", ""),
        "temp_c": now.get("temp", ""),
        "feels_like_c": now.get("feelsLike", ""),
        "humidity": now.get("humidity", ""),
    }


def _request_json(
    url: str,
    headers: dict[str, str],
    timeout_sec: float,
    opener: Callable[..., Any],
) -> dict[str, Any]:
    request = urllib.request.Request(url, headers=headers)
    try:
        with opener(request, timeout=timeout_sec) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"和风天气 HTTP {exc.code}: {body or exc.reason}") from exc
    code = str(payload.get("code", "200"))
    if code != "200":
        raise RuntimeError(f"和风天气返回 code={code}。")
    return payload
