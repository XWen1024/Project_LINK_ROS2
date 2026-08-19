"""Cancelable OpenAI-compatible vision client for fall assessment."""

from __future__ import annotations

import base64
import json

import aiohttp

from .core import FallAssessment, FallAssessmentError, parse_fall_assessment_json


def build_openai_vision_payload(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    images: list[tuple[str, bytes]],
) -> dict:
    if not images:
        raise FallAssessmentError("vision model request has no images")
    content = [{"type": "text", "text": user_prompt}]
    for label, jpeg_data in images:
        encoded = base64.b64encode(jpeg_data).decode("ascii")
        content.append({"type": "text", "text": str(label)})
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}}
        )
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ],
        "temperature": 0,
    }


class AsyncOpenAICompatibleVisionClient:
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
        return await self.assess_many([("camera image", jpeg_data)])

    async def assess_many(self, images: list[tuple[str, bytes]]) -> FallAssessment:
        if not self.api_key:
            raise FallAssessmentError("OPENAI_API_KEY is not configured")
        payload = build_openai_vision_payload(
            model=self.model,
            system_prompt=self.system_prompt,
            user_prompt=self.user_prompt,
            images=images,
        )
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


AsyncSiliconFlowVisionClient = AsyncOpenAICompatibleVisionClient
