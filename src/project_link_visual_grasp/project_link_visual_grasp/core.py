"""Hardware-independent core for the Project LINK visual grasp node."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import logging
from pathlib import Path
import threading
import time
from typing import Any, Optional

import numpy as np
import yaml

LOGGER = logging.getLogger(__name__)
ARM_JOINTS = (
    "shoulder_pan.pos",
    "shoulder_lift.pos",
    "elbow_flex.pos",
    "wrist_flex.pos",
    "wrist_roll.pos",
)
ALL_JOINTS = ARM_JOINTS + ("gripper.pos",)


class TrackerState(str, Enum):
    IDLE = "IDLE"
    LOADING = "LOADING"
    TRACKING = "TRACKING"
    LOST = "LOST"
    ERROR = "ERROR"


class ServoState(str, Enum):
    IDLE = "IDLE"
    TRACKING = "TRACKING"
    CENTERING = "CENTERING"
    APPROACHING = "APPROACHING"
    MOVING = "MOVING"
    GRASPED = "GRASPED"
    ERROR = "ERROR"


@dataclass(frozen=True)
class Detection:
    bbox: tuple[int, int, int, int]
    confidence: float


class RuntimeStore:
    """Stores operator tuning outside the Git checkout."""

    def __init__(self, config_path: str, positions_path: str):
        self.config_path = Path(config_path).expanduser()
        self.positions_path = Path(positions_path).expanduser()

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)

    def load_overrides(self) -> dict[str, Any]:
        if not self.config_path.exists():
            return {}
        data = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}

    def save_overrides(self, values: dict[str, Any]) -> None:
        self._atomic_write(
            self.config_path,
            yaml.safe_dump(values, allow_unicode=True, sort_keys=True),
        )

    def load_positions(self) -> dict[str, dict[str, float]]:
        if not self.positions_path.exists():
            return {}
        try:
            data = json.loads(self.positions_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.warning("Ignoring invalid saved positions: %s", exc)
            return {}
        return data if isinstance(data, dict) else {}

    def save_positions(self, positions: dict[str, dict[str, float]]) -> None:
        self._atomic_write(
            self.positions_path,
            json.dumps(positions, ensure_ascii=False, indent=2) + "\n",
        )


class YoloWorldTracker:
    """Asynchronous local YOLO-World detector with a single active prompt."""

    def __init__(self, model_path: str, config: dict[str, Any]):
        self._model_path = model_path
        self._config = dict(config)
        self._model: Any = None
        self._model_error = ""
        self._target = ""
        self._state = TrackerState.LOADING
        self._latest_frame: Optional[np.ndarray] = None
        self._latest_detection: Optional[Detection] = None
        self._lost_frames = 0
        self._ema_bbox: Optional[np.ndarray] = None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._running = True
        self._last_infer = 0.0
        self._message = "YOLO-World model loading"
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    @property
    def state(self) -> TrackerState:
        with self._lock:
            return self._state

    @property
    def message(self) -> str:
        with self._lock:
            return self._message

    @property
    def target(self) -> str:
        with self._lock:
            return self._target

    @property
    def model_ready(self) -> bool:
        with self._lock:
            return self._model is not None

    def update_config(self, config: dict[str, Any]) -> None:
        with self._lock:
            self._config.update(config)

    def set_target(self, target: str) -> tuple[bool, str]:
        target = target.strip()
        if not target:
            return False, "Target text cannot be empty"
        with self._lock:
            if self._model is None:
                return False, self._model_error or "YOLO-World model is still loading"
            try:
                self._model.set_classes([target])
            except Exception as exc:
                return False, f"Unable to set YOLO-World classes: {exc}"
            self._target = target
            self._latest_detection = None
            self._ema_bbox = None
            self._lost_frames = 0
            self._state = TrackerState.TRACKING
            self._message = f"Tracking {target}"
        return True, f"Tracking {target}"

    def clear_target(self) -> None:
        with self._lock:
            self._target = ""
            self._latest_detection = None
            self._ema_bbox = None
            self._lost_frames = 0
            self._state = TrackerState.IDLE if self._model else TrackerState.LOADING
            self._message = "Target cleared"

    def submit(self, frame: np.ndarray) -> Optional[Detection]:
        with self._lock:
            if self._target and self._model is not None:
                self._latest_frame = frame.copy()
                self._wake.set()
            return self._latest_detection

    def stop(self) -> None:
        self._running = False
        self._wake.set()
        self._thread.join(timeout=2.0)

    def _worker(self) -> None:
        self._load_model()
        while self._running:
            self._wake.wait(timeout=0.25)
            self._wake.clear()
            with self._lock:
                frame = self._latest_frame
                target = self._target
                model = self._model
                interval = float(self._config.get("yolo_infer_interval_sec", 0.0))
            if frame is None or not target or model is None:
                continue
            now = time.monotonic()
            if interval > 0.0 and now - self._last_infer < interval:
                continue
            self._last_infer = now
            self._infer(model, frame)

    def _load_model(self) -> None:
        try:
            from ultralytics import YOLO

            model = YOLO(self._model_path)
            model.predict(np.zeros((32, 32, 3), dtype=np.uint8), verbose=False)
            with self._lock:
                self._model = model
                self._state = TrackerState.IDLE
                self._message = "YOLO-World model ready"
            LOGGER.info("YOLO-World model ready: %s", self._model_path)
        except Exception as exc:
            LOGGER.exception("YOLO-World model load failed")
            with self._lock:
                self._model_error = str(exc)
                self._state = TrackerState.ERROR
                self._message = f"YOLO-World model load failed: {exc}"

    def _infer(self, model: Any, frame: np.ndarray) -> None:
        try:
            with self._lock:
                threshold = float(self._config.get("yolo_conf_threshold", 0.15))
                alpha = float(self._config.get("yolo_ema_alpha", 0.6))
                max_lost = int(self._config.get("yolo_max_lost_frames", 15))
            result = model.predict(frame, conf=threshold, verbose=False)[0]
            boxes = result.boxes
            detection: Optional[Detection] = None
            if boxes is not None and len(boxes) > 0:
                confidences = boxes.conf.detach().cpu().numpy()
                index = int(np.argmax(confidences))
                x1, y1, x2, y2 = boxes.xyxy[index].detach().cpu().numpy()
                raw_bbox = np.array(
                    [x1, y1, max(1.0, x2 - x1), max(1.0, y2 - y1)], dtype=float
                )
                with self._lock:
                    self._ema_bbox = (
                        raw_bbox
                        if self._ema_bbox is None
                        else alpha * raw_bbox + (1.0 - alpha) * self._ema_bbox
                    )
                    bbox = tuple(int(value) for value in self._ema_bbox)
                    detection = Detection(bbox=bbox, confidence=float(confidences[index]))
                    self._latest_detection = detection
                    self._lost_frames = 0
                    self._state = TrackerState.TRACKING
                    self._message = f"Tracking {self._target}"
            else:
                with self._lock:
                    self._lost_frames += 1
                    if self._lost_frames >= max_lost:
                        self._latest_detection = None
                        self._state = TrackerState.LOST
                        self._message = f"Lost {self._target}"
        except Exception as exc:
            LOGGER.exception("YOLO-World inference failed")
            with self._lock:
                self._state = TrackerState.ERROR
                self._message = f"YOLO-World inference failed: {exc}"


class SO101Arm:
    """Thin Linux-safe wrapper around LeRobot's SO-101 follower."""

    def __init__(self) -> None:
        self._robot: Any = None
        self._torque_enabled = False

    @property
    def connected(self) -> bool:
        return self._robot is not None and bool(getattr(self._robot, "is_connected", False))

    @property
    def torque_enabled(self) -> bool:
        return self._torque_enabled

    def connect(self, port: str, robot_id: str) -> tuple[bool, str]:
        if self.connected:
            return True, "SO-101 is already connected"
        try:
            from lerobot.robots.so101_follower.config_so101_follower import SO101FollowerConfig
            from lerobot.robots.so101_follower.so101_follower import SO101Follower

            robot = SO101Follower(SO101FollowerConfig(port=port, id=robot_id))
            robot.connect(calibrate=True)
            self._robot = robot
            self._torque_enabled = False
            return True, f"Connected to SO-101 on {port}"
        except Exception as exc:
            LOGGER.exception("SO-101 connection failed")
            self._robot = None
            return False, f"SO-101 connection failed: {exc}"

    def disconnect(self) -> tuple[bool, str]:
        if not self._robot:
            return True, "SO-101 is already disconnected"
        try:
            self.disable_torque()
            self._robot.disconnect()
            return True, "SO-101 disconnected"
        except Exception as exc:
            return False, f"SO-101 disconnect failed: {exc}"
        finally:
            self._robot = None
            self._torque_enabled = False

    def enable_torque(self) -> tuple[bool, str]:
        if not self.connected:
            return False, "SO-101 is not connected"
        try:
            self._robot.bus.enable_torque()
        except Exception as first_error:
            LOGGER.warning("Full torque enable failed: %s", first_error)
            try:
                for motor in (name.removesuffix(".pos") for name in ARM_JOINTS):
                    self._robot.bus.enable_torque(motors=motor)
            except Exception as exc:
                return False, f"SO-101 torque enable failed: {exc}"
        self._torque_enabled = True
        return True, "SO-101 torque enabled"

    def disable_torque(self) -> tuple[bool, str]:
        if not self.connected:
            return True, "SO-101 is not connected"
        try:
            self._robot.bus.disable_torque()
            self._torque_enabled = False
            return True, "SO-101 torque disabled"
        except Exception as exc:
            return False, f"SO-101 torque disable failed: {exc}"

    def get_joints(self) -> dict[str, float]:
        if not self.connected:
            return {}
        try:
            observation = self._robot.get_observation()
            return {name: float(observation[name]) for name in ALL_JOINTS if name in observation}
        except Exception as exc:
            LOGGER.warning("Unable to read SO-101 joints: %s", exc)
            return {}

    def send_arm_joints(self, desired: dict[str, float]) -> tuple[bool, str]:
        if not self.connected:
            return False, "SO-101 is not connected"
        current = self.get_joints()
        if not current:
            return False, "Unable to read SO-101 joint positions"
        action = {name: float(desired.get(name, current.get(name, 0.0))) for name in ARM_JOINTS}
        try:
            self._robot.send_action(action)
            return True, "Arm command sent"
        except Exception as exc:
            return False, f"Arm command failed: {exc}"

    def set_gripper(self, position: float) -> tuple[bool, str]:
        if not self.connected:
            return False, "SO-101 is not connected"
        try:
            self._robot.send_action({"gripper.pos": float(position)})
            return True, "Gripper command sent"
        except Exception as exc:
            return False, f"Gripper command failed: {exc}"


class VisualServoController:
    """2D image-space visual servo controller copied from the validated tracker."""

    def __init__(self, arm: SO101Arm, config: dict[str, Any], positions: dict[str, dict[str, float]]):
        self.arm = arm
        self.config = dict(config)
        self.positions = dict(positions)
        self.state = ServoState.IDLE
        self.message = "Idle"
        self._move_target: Optional[dict[str, float]] = None
        self._move_pan_pending: Optional[float] = None
        self._move_started = 0.0
        self.demo_recording = False
        self.demo_rows: list[dict[str, Any]] = []

    def update_config(self, config: dict[str, Any]) -> None:
        self.config.update(config)

    def set_tracking(self) -> None:
        if self.state not in (ServoState.CENTERING, ServoState.APPROACHING, ServoState.MOVING):
            self.state = ServoState.TRACKING
            self.message = "Tracking; waiting for manual grasp command"

    def start_approach(self) -> tuple[bool, str]:
        if not self.arm.connected:
            return False, "SO-101 is not connected"
        if not self.arm.torque_enabled:
            return False, "Enable SO-101 torque before grasping"
        self.state = ServoState.CENTERING
        self.message = "Centering target"
        return True, self.message

    def stop(self) -> tuple[bool, str]:
        self._move_target = None
        self._move_pan_pending = None
        self.state = ServoState.TRACKING
        self.message = "Motion stopped; tracking remains active"
        return True, self.message

    def update(self, detection: Optional[Detection], frame_size: tuple[int, int]) -> None:
        if self.demo_recording:
            self._record_demo(detection)
            return
        if self.state == ServoState.MOVING:
            self._tick_move()
            return
        if self.state not in (ServoState.CENTERING, ServoState.APPROACHING):
            return
        if detection is None:
            self.message = "Target lost while grasping"
            return
        if self.state == ServoState.CENTERING:
            self._tick_center(detection, frame_size)
        elif self.state == ServoState.APPROACHING:
            self._tick_approach(detection, frame_size)

    def record_position(self, name: str) -> tuple[bool, str]:
        joints = self.arm.get_joints()
        if not joints:
            return False, "Unable to read SO-101 joints"
        self.positions[name] = joints
        return True, f"Recorded {name} position"

    def go_to_position(self, name: str) -> tuple[bool, str]:
        target = self.positions.get(name)
        if not target:
            return False, f"No saved {name} position"
        if not self.arm.connected or not self.arm.torque_enabled:
            return False, "Connect arm and enable torque first"
        self._move_target = dict(target)
        self._move_pan_pending = float(target.get("shoulder_pan.pos", 0.0))
        self._move_started = time.monotonic()
        self.state = ServoState.MOVING
        self.message = f"Moving to {name}"
        return True, self.message

    def _tick_center(self, detection: Detection, frame_size: tuple[int, int]) -> None:
        width, height = frame_size
        x, y, box_width, box_height = detection.bbox
        center_x = x + box_width / 2.0
        center_y = y + box_height / 2.0
        error_x = (center_x - (width / 2.0 + float(self.config["center_offset_x"]))) / width
        error_y = (center_y - (height / 2.0 + float(self.config["center_offset_y"]))) / height
        if max(abs(error_x), abs(error_y)) <= float(self.config["centering_threshold"]):
            self.state = ServoState.APPROACHING
            self.message = "Target centered; approaching"
            return
        joints = self.arm.get_joints()
        if not joints:
            self.state = ServoState.ERROR
            self.message = "Unable to read joints for centering"
            return
        desired = dict(joints)
        desired["shoulder_pan.pos"] = joints["shoulder_pan.pos"] + float(self.config["pan_gain"]) * error_x
        desired["shoulder_lift.pos"] = joints["shoulder_lift.pos"] + float(self.config["tilt_gain"]) * error_y
        ok, message = self.arm.send_arm_joints(desired)
        if not ok:
            self.state = ServoState.ERROR
            self.message = message

    def _tick_approach(self, detection: Detection, frame_size: tuple[int, int]) -> None:
        width, height = frame_size
        _, _, box_width, box_height = detection.bbox
        if box_width * box_height / float(width * height) >= float(self.config["grasp_area_threshold"]):
            ok, message = self.arm.set_gripper(float(self.config["gripper_close"]))
            self.state = ServoState.GRASPED if ok else ServoState.ERROR
            self.message = "Grasp complete" if ok else message
            return
        joints = self.arm.get_joints()
        if not joints:
            self.state = ServoState.ERROR
            self.message = "Unable to read joints for approach"
            return
        lift = joints["shoulder_lift.pos"] + float(self.config["approach_step"])
        desired = dict(joints)
        desired["shoulder_lift.pos"] = lift
        desired["elbow_flex.pos"] = 0.001089 * lift * lift - 1.023 * lift - 5.55
        ok, message = self.arm.send_arm_joints(desired)
        if not ok:
            self.state = ServoState.ERROR
            self.message = message

    def _tick_move(self) -> None:
        if self._move_target is None:
            self.state = ServoState.IDLE
            return
        if time.monotonic() - self._move_started > float(self.config["move_timeout_sec"]):
            self.state = ServoState.ERROR
            self.message = "Preset move timed out"
            return
        joints = self.arm.get_joints()
        if not joints:
            self.state = ServoState.ERROR
            self.message = "Unable to read joints during preset move"
            return
        threshold = float(self.config["arrive_threshold"])
        step = float(self.config["move_step_limit"])
        desired = dict(joints)
        non_pan_done = True
        for name in ARM_JOINTS:
            if name == "shoulder_pan.pos":
                continue
            delta = float(self._move_target.get(name, joints[name])) - joints[name]
            if abs(delta) > threshold:
                desired[name] = joints[name] + max(-step, min(step, delta))
                non_pan_done = False
        if non_pan_done and self._move_pan_pending is not None:
            delta = self._move_pan_pending - joints["shoulder_pan.pos"]
            if abs(delta) > threshold:
                desired["shoulder_pan.pos"] = joints["shoulder_pan.pos"] + max(-step, min(step, delta))
                non_pan_done = False
            else:
                self._move_pan_pending = None
        if non_pan_done and self._move_pan_pending is None:
            self.state = ServoState.IDLE
            self.message = "Preset move complete"
            return
        ok, message = self.arm.send_arm_joints(desired)
        if not ok:
            self.state = ServoState.ERROR
            self.message = message

    def _record_demo(self, detection: Optional[Detection]) -> None:
        joints = self.arm.get_joints()
        if not joints:
            return
        self.demo_rows.append({
            "time": time.time(),
            "state": self.state.value,
            "bbox": detection.bbox if detection else None,
            "confidence": detection.confidence if detection else 0.0,
            "joints": joints,
        })