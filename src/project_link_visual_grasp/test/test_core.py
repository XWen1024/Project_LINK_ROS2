from pathlib import Path

from project_link_visual_grasp.core import RuntimeStore


def test_runtime_store_round_trip(tmp_path: Path):
    store = RuntimeStore(str(tmp_path / "override.yaml"), str(tmp_path / "positions.json"))
    store.save_overrides({"pan_gain": 12.5, "camera_device": "/dev/video0"})
    assert store.load_overrides()["pan_gain"] == 12.5
    positions = {"standby": {"shoulder_pan.pos": 1.0}}
    store.save_positions(positions)
    assert store.load_positions() == positions