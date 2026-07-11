"""Local task parsing for guarded voice commands."""

from __future__ import annotations

from dataclasses import dataclass

from .waypoints import Waypoint, WaypointStore


FETCH_WORDS = ("拿", "取", "抓", "带", "找")
TRAILING_WORDS = ("给我", "回来", "回來", "一下", "可以吗", "可以嗎", "吧", "。", "，")


@dataclass(frozen=True)
class FetchTask:
    waypoint: Waypoint
    spoken_item: str
    grasp_target: str


def parse_aliases(values: list[str]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            continue
        spoken, target = value.split("=", 1)
        spoken = spoken.strip()
        target = target.strip()
        if spoken and target:
            aliases[spoken] = target
    return aliases


def parse_fetch_task(text: str, store: WaypointStore, aliases: dict[str, str]) -> FetchTask | None:
    waypoint = store.find_in_text(text)
    if waypoint is None or not any(word in text for word in FETCH_WORDS):
        return None

    for spoken, target in sorted(aliases.items(), key=lambda item: len(item[0]), reverse=True):
        if spoken in text:
            return FetchTask(waypoint=waypoint, spoken_item=spoken, grasp_target=target)

    item = _extract_item_text(text, waypoint.name)
    if not item:
        return None
    return FetchTask(waypoint=waypoint, spoken_item=item, grasp_target=item)


def _extract_item_text(text: str, waypoint_name: str) -> str:
    best_index = -1
    for word in FETCH_WORDS:
        index = text.rfind(word)
        if index > best_index:
            best_index = index + len(word)
    if best_index < 0:
        return ""
    item = text[best_index:].replace(waypoint_name, "")
    for word in TRAILING_WORDS:
        item = item.replace(word, "")
    return item.strip()
