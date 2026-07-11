import json

from project_link_voice.task_parser import parse_aliases, parse_fetch_task
from project_link_voice.waypoints import WaypointStore


def make_store(tmp_path):
    default_file = tmp_path / "waypoints.json"
    default_file.write_text(json.dumps({"厨房": {"x": 1, "y": 2, "yaw": 0}}), encoding="utf-8")
    return WaypointStore(default_file)


def test_fetch_task_uses_alias_target(tmp_path):
    store = make_store(tmp_path)
    aliases = parse_aliases(["药瓶=medicine bottle"])
    task = parse_fetch_task("去厨房拿药瓶", store, aliases)
    assert task is not None
    assert task.waypoint.name == "厨房"
    assert task.spoken_item == "药瓶"
    assert task.grasp_target == "medicine bottle"


def test_fetch_task_extracts_unknown_item_text(tmp_path):
    store = make_store(tmp_path)
    task = parse_fetch_task("到厨房取蓝色盒子回来", store, {})
    assert task is not None
    assert task.grasp_target == "蓝色盒子"


def test_fetch_task_requires_waypoint_and_fetch_word(tmp_path):
    store = make_store(tmp_path)
    assert parse_fetch_task("去厨房", store, {}) is None
    assert parse_fetch_task("拿药瓶", store, {}) is None
