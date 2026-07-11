"""Pure-Python helpers for fall assessment and Feishu notification."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
import time
from typing import Any
from urllib import error, request


@dataclass(frozen=True)
class FallAssessment:
    fall_suspected: bool
    confidence: float
    reason: str


@dataclass(frozen=True)
class NotificationResult:
    attempted: bool
    success: bool
    message: str


class FallAssessmentError(RuntimeError):
    """Raised when the vision assessment cannot produce a trusted result."""


def parse_fall_assessment_json(content: str) -> FallAssessment:
    """Parse the strict JSON contract returned by the vision model."""
    try:
        payload = json.loads(content.strip())
    except json.JSONDecodeError as exc:
        raise FallAssessmentError(f"vision model returned invalid JSON: {exc.msg}") from exc

    if not isinstance(payload, dict):
        raise FallAssessmentError("vision model JSON must be an object")
    if not isinstance(payload.get("fall_suspected"), bool):
        raise FallAssessmentError("vision model JSON missing boolean fall_suspected")
    confidence = payload.get("confidence")
    if not isinstance(confidence, (int, float)):
        raise FallAssessmentError("vision model JSON missing numeric confidence")
    confidence_value = float(confidence)
    if confidence_value < 0.0 or confidence_value > 1.0:
        raise FallAssessmentError("vision model confidence must be between 0 and 1")
    reason = payload.get("reason")
    if not isinstance(reason, str):
        raise FallAssessmentError("vision model JSON missing string reason")
    return FallAssessment(
        fall_suspected=bool(payload["fall_suspected"]),
        confidence=confidence_value,
        reason=reason.strip(),
    )


def image_message_content(jpeg_data: bytes, prompt: str) -> list[dict[str, Any]]:
    encoded = base64.b64encode(jpeg_data).decode("ascii")
    return [
        {"type": "text", "text": prompt},
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
        },
    ]


class SiliconFlowVisionClient:
    def __init__(
        self,
        api_key: str | None,
        base_url: str,
        model: str,
        request_timeout_sec: float,
        system_prompt: str,
        user_prompt: str,
    ) -> None:
        self._api_key = api_key or ""
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._request_timeout_sec = float(request_timeout_sec)
        self._system_prompt = system_prompt
        self._user_prompt = user_prompt

    @classmethod
    def from_environment(
        cls,
        base_url: str,
        model: str,
        request_timeout_sec: float,
        system_prompt: str,
        user_prompt: str,
    ) -> "SiliconFlowVisionClient":
        return cls(
            api_key=os.environ.get("SILICONFLOW_API_KEY"),
            base_url=base_url,
            model=model,
            request_timeout_sec=request_timeout_sec,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

    def assess(self, jpeg_data: bytes) -> FallAssessment:
        if not self._api_key:
            raise FallAssessmentError("SILICONFLOW_API_KEY is not configured")
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": image_message_content(jpeg_data, self._user_prompt)},
            ],
            "temperature": 0,
        }
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            f"{self._base_url}/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self._request_timeout_sec) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            raise FallAssessmentError(f"vision model HTTP error: {exc.code}") from exc
        except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise FallAssessmentError(f"vision model request failed: {exc}") from exc

        try:
            content = response_payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise FallAssessmentError("vision model response missing choices[0].message.content") from exc
        if not isinstance(content, str):
            raise FallAssessmentError("vision model content is not text")
        return parse_fall_assessment_json(content)


def missing_values(names: list[str], env: dict[str, str] | None = None) -> list[str]:
    values = env if env is not None else os.environ
    return [name for name in names if not values.get(name)]


def redact_secrets(message: str, secrets: list[str]) -> str:
    redacted = message
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "***")
    return redacted


def feishu_signature(timestamp: str, secret: str) -> str:
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


class FeishuBotClient:
    REQUIRED_ENV = ["FEISHU_BOT_WEBHOOK"]

    def __init__(self, env: dict[str, str] | None = None) -> None:
        self._env = env if env is not None else os.environ

    def send_fall_alert(self, alert_id: str, confidence: float, reason: str) -> NotificationResult:
        missing = missing_values(self.REQUIRED_ENV, self._env)
        if missing:
            return NotificationResult(
                attempted=False,
                success=False,
                message=f"missing Feishu bot environment: {', '.join(missing)}",
            )
        webhook = self._env["FEISHU_BOT_WEBHOOK"]
        secret = self._env.get("FEISHU_BOT_SECRET", "")
        title = self._env.get("FEISHU_BOT_ALERT_TITLE", "Project LINK 跌倒告警")
        text = (
            f"{title}\n"
            f"疑似跌倒已触发紧急通知。\n"
            f"alert_id: {alert_id}\n"
            f"confidence: {confidence:.2f}\n"
            f"reason: {reason or '无'}"
        )
        payload: dict[str, Any] = {
            "msg_type": "text",
            "content": {"text": text},
        }
        if secret:
            timestamp = str(int(time.time()))
            payload["timestamp"] = timestamp
            payload["sign"] = feishu_signature(timestamp, secret)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            webhook,
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        secrets = [webhook, secret]
        try:
            with request.urlopen(req, timeout=float(self._env.get("FEISHU_BOT_TIMEOUT_SEC", "5"))) as response:
                response_text = response.read().decode("utf-8")
            payload = json.loads(response_text) if response_text else {}
            code = payload.get("code", payload.get("StatusCode", 0))
            message = payload.get("msg", payload.get("StatusMessage", "sent"))
            success = int(code) == 0
            return NotificationResult(
                attempted=True,
                success=success,
                message=str(message or ("sent" if success else f"Feishu bot returned code {code}")),
            )
        except (error.HTTPError, error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            message = redact_secrets(str(exc), secrets)
            return NotificationResult(attempted=True, success=False, message=message)
