"""Dependency preflight for the Windows visual grasp lab."""
from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path


REQUIRED_SPECS = (
    ("PySide6", "PySide6"),
    ("OpenCV", "cv2"),
    ("NumPy", "numpy"),
    ("PySerial", "serial"),
    ("Ultralytics", "ultralytics"),
    ("DeepDiff", "deepdiff"),
    ("Draccus", "draccus"),
    ("Feetech SDK", "scservo_sdk"),
    ("LeRobot", "lerobot"),
)


def main() -> None:
    missing = []
    for label, module_name in REQUIRED_SPECS:
        if importlib.util.find_spec(module_name) is None:
            missing.append(f"{label}: module '{module_name}' was not found")
    lerobot_spec = importlib.util.find_spec("lerobot")
    if lerobot_spec is not None and lerobot_spec.submodule_search_locations:
        lerobot_root = Path(next(iter(lerobot_spec.submodule_search_locations)))
        for label, relative_path in (
            (
                "LeRobot SO-101 config",
                Path("robots/so101_follower/config_so101_follower.py"),
            ),
            (
                "LeRobot SO-101 driver",
                Path("robots/so101_follower/so101_follower.py"),
            ),
        ):
            if not (lerobot_root / relative_path).is_file():
                missing.append(f"{label}: file '{lerobot_root / relative_path}' was not found")
    if missing:
        raise SystemExit("Missing dependencies:\n" + "\n".join(missing))
    importlib.import_module("app")
    print("Windows visual grasp lab dependencies are ready.")


if __name__ == "__main__":
    main()
