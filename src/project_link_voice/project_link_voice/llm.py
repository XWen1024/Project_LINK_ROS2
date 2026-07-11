"""Optional cloud chat response path. It never generates motion commands."""

from __future__ import annotations

import os


class ChatResponder:
    def __init__(self, enabled: bool, base_url: str, model: str) -> None:
        self._enabled = enabled
        self._base_url = base_url
        self._model = model

    def respond(self, user_text: str) -> str | None:
        if not self._enabled:
            return None
        api_key = os.environ.get("SILICONFLOW_API_KEY")
        if not api_key:
            return "闲聊服务未配置密钥。运动命令仍可使用已保存地点、确认和取消。"
        try:
            from openai import OpenAI

            client = OpenAI(api_key=api_key, base_url=self._base_url)
            response = client.chat.completions.create(
                model=self._model,
                messages=[
                    {
                        "role": "system",
                        "content": "你是机器人语音助手。只回答日常问题，不得输出、建议或调用任何运动控制命令。回复简短、适合播报。",
                    },
                    {"role": "user", "content": user_text},
                ],
            )
            return (response.choices[0].message.content or "").strip() or None
        except Exception:
            return "闲聊服务暂时不可用。"