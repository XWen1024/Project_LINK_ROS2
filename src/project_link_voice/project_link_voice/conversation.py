"""Pure helpers for bounded continuous voice conversations."""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable


DEFAULT_EXIT_KEYWORDS = (
    "停止",
    "停下",
    "停一下",
    "停一停",
    "急停",
    "别动",
    "取消",
    "退出",
    "退出对话",
    "关闭对话",
    "结束对话",
    "结束聊天",
    "退下",
    "休息",
    "休息吧",
    "不用了",
    "不要了",
    "算了",
    "别说了",
    "再见",
    "拜拜",
)


def normalize_spoken_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(text)).casefold()
    return "".join(character for character in normalized if character.isalnum())


def is_conversation_exit(text: str, keywords: Iterable[str] = DEFAULT_EXIT_KEYWORDS) -> bool:
    normalized = normalize_spoken_text(text)
    if not normalized:
        return False
    return any(
        normalized_keyword and normalized_keyword in normalized
        for normalized_keyword in (normalize_spoken_text(keyword) for keyword in keywords)
    )


def conversation_limit_reason(
    completed_turns: int,
    elapsed_sec: float,
    max_turns: int,
    max_session_sec: float,
) -> str | None:
    if max_turns > 0 and completed_turns >= max_turns:
        return "max_turns"
    if max_session_sec > 0.0 and elapsed_sec >= max_session_sec:
        return "max_session"
    return None
