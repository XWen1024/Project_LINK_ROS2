import json

from project_link_voice.waypoints import ConfirmationState, WaypointStore


def make_store(tmp_path):
    default_file = tmp_path / "waypoints.json"
    default_file.write_text(json.dumps({"客厅": {"x": 1, "y": 2, "yaw": 0}, "大客厅": {"x": 3, "y": 4, "yaw": 0}}), encoding="utf-8")
    return WaypointStore(default_file)


def test_longest_named_waypoint_wins(tmp_path):
    store = make_store(tmp_path)
    waypoint = store.find_in_text("去大客厅")
    assert waypoint is not None
    assert waypoint.name == "大客厅"


def test_confirmation_requires_target_then_confirmation(tmp_path):
    store = make_store(tmp_path)
    state = ConfirmationState()
    command, waypoint = state.consume("去客厅", store)
    assert (command, waypoint.name) == ("target", "客厅")
    command, waypoint = state.consume("确认前往", store)
    assert (command, waypoint.name) == ("confirm", "客厅")
    assert state.driving


def test_cancel_has_priority(tmp_path):
    store = make_store(tmp_path)
    state = ConfirmationState()
    state.consume("去客厅", store)
    command, waypoint = state.consume("取消去客厅", store)
    assert command == "cancel"
    assert waypoint is None
    assert state.pending_waypoint is None