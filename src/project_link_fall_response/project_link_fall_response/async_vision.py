"""Cancelable aiohttp client for SiliconFlow fall assessment."""

from __future__ import annotations

import base64
import json

import aiohttp

from .core import FallAssessment, FallAssessmentError, parse_fall_assessment_json


class AsyncSiliconFlowVisionClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_sec: float,
        system_prompt: str,
        user_prompt: str,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_sec = float(timeout_sec)
        self.system_prompt = system_prompt
        self.user_prompt = user_prompt

    @property
    def ready(self) -> bool:
        return bool(self.api_key)

    async def assess(self, jpeg_data: bytes) -> FallAssessment:
        if not self.api_key:
            raise FallAssessmentError("SILICONFLOW_API_KEY is not configured")
        encoded = base64.b64encode(jpeg_data).decode("ascii")
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": self.user_prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}},
                    ],
                },
            ],
            "temperature": 0,
        }
        timeout = aiohttp.ClientTimeout(total=self.timeout_sec)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                ) as response:
                    if response.status >= 400:
                        raise FallAssessmentError(f"vision model HTTP error: {response.status}")
                    response_payload = await response.json()
        except FallAssessmentError:
            raise
        except (aiohttp.ClientError, TimeoutError, json.JSONDecodeError) as exc:
            raise FallAssessmentError(f"vision model request failed: {exc}") from exc
        try:
            content = response_payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise FallAssessmentError("vision model response missing choices[0].message.content") from exc
        if not isinstance(content, str):
            raise FallAssessmentError("vision model content is not text")
        return parse_fall_assessment_json(content)
