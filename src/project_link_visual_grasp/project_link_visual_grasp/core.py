"""Hardware-independent core for the Project LINK visual grasp node."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
from functools import wraps
import json
import logging
from pathlib import Path
from statistics import median
import threading
import time
from typing import Any, Callable, Optional

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
DEMO_CSV_FIELDS = (
    "sample_index",
    "time_unix",
    "elapsed_sec",
    "target",
    "state",
    "frame_width",
    "frame_height",
    "target_center_x",
    "target_center_y",
    "detection_present",
    "detection_trusted",
    "detection_sequence",
    "confidence",
    "bbox_x",
    "bbox_y",
    "bbox_width",
    "bbox_height",
    "bbox_center_x",
    "bbox_center_y",
    "bbox_area_px",
    "bbox_area_ratio",
    "error_x",
    "error_y",
    "tof_range_m",
    "tof_age_sec",
    "tof_valid",
    "tof_reason",
    "shoulder_pan_pos",
    "shoulder_lift_pos",
    "elbow_flex_pos",
    "wrist_flex_pos",
    "wrist_roll_pos",
    "gripper_pos",
)


def serialized_arm_io(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._io_lock:
            return method(self, *args, **kwargs)

    return wrapped


def decode_feetech_position(value: int) -> int:
    value = int(value)
    if value & 0x8000:
        return -(value & 0x7FFF)
    return value


def recenter_feetech_calibration_range(
    homing_offset: int,
    range_min: int,
    range_max: int,
    resolution: int,
) -> tuple[int, int, int]:
    max_position = int(resolution) - 1
    max_offset = max_position // 2
    if range_max <= range_min:
        raise ValueError(f"Invalid range {range_min}..{range_max}")
    if range_max - range_min > max_position:
        raise ValueError(
            f"Recorded range span {range_max - range_min} exceeds encoder span {max_position}"
        )
    minimum_shift = max(-range_min, homing_offset - max_offset)
    maximum_shift = min(max_position - range_max, homing_offset + max_offset)
    if minimum_shift > maximum_shift:
        raise ValueError(
            "Recorded range cannot fit the encoder limits while keeping a valid homing offset"
        )
    target_shift = round(max_position / 2 - (range_min + range_max) / 2)
    shift = min(maximum_shift, max(minimum_shift, target_shift))
    adjusted_offset = homing_offset - shift
    adjusted_min = range_min + shift
    adjusted_max = range_max + shift
    return adjusted_offset, adjusted_min, adjusted_max


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
    FINAL_APPROACH = "FINAL_APPROACH"
    RANGE_WAIT = "RANGE_WAIT"
    MOVING = "MOVING"
    GRASPED = "GRASPED"
    ERROR = "ERROR"


@dataclass(frozen=True)
class Detection:
    bbox: tuple[int, int, int, int]
    confidence: float
    trusted: bool = True
    sequence: int = 0


@dataclass(frozen=True)
class TofReading:
    range_m: float | None
    age_sec: float
    valid: bool
    reason: str


def bbox_iou(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> float:
    first_x, first_y, first_width, first_height = first
    second_x, second_y, second_width, second_height = second
    left = max(first_x, second_x)
    top = max(first_y, second_y)
    right = min(first_x + first_width, second_x + second_width)
    bottom = min(first_y + first_height, second_y + second_height)
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    union = first_width * first_height + second_width * second_height - intersection
    return intersection / union if union > 0.0 else 0.0


def bbox_center_jump_ratio(
    previous: tuple[float, float, float, float],
    candidate: tuple[float, float, float, float],
    frame_size: tuple[int, int],
) -> float:
    previous_center_x = previous[0] + previous[2] / 2.0
    previous_center_y = previous[1] + previous[3] / 2.0
    candidate_center_x = candidate[0] + candidate[2] / 2.0
    candidate_center_y = candidate[1] + candidate[3] / 2.0
    distance = (
        (candidate_center_x - previous_center_x) ** 2
        + (candidate_center_y - previous_center_y) ** 2
    ) ** 0.5
    diagonal = max(1.0, (frame_size[0] ** 2 + frame_size[1] ** 2) ** 0.5)
    return distance / diagonal


def bbox_area_change_ratio(
    previous: tuple[float, float, float, float],
    candidate: tuple[float, float, float, float],
) -> float:
    previous_area = max(1.0, previous[2] * previous[3])
    candidate_area = max(1.0, candidate[2] * candidate[3])
    return max(previous_area / candidate_area, candidate_area / previous_area)


class CalibrationRangeRecorder:
    """Accumulate decoded encoder minima and maxima during remote calibration."""

    def __init__(self, positions: dict[str, int]):
        self.minimums = dict(positions)
        self.maximums = dict(positions)

    def update(self, positions: dict[str, int]) -> None:
        for motor, position in positions.items():
            self.minimums[motor] = min(self.minimums[motor], position)
            self.maximums[motor] = max(self.maximums[motor], position)

    def result(self) -> tuple[dict[str, int], dict[str, int]]:
        unchanged = [
            motor
            for motor in self.minimums
            if self.minimums[motor] == self.maximums[motor]
        ]
        if unchanged:
            raise ValueError(f"Joints did not move through a range: {unchanged}")
        return dict(self.minimums), dict(self.maximums)


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
        self._outlier_frames = 0
        self._detection_sequence = 0
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
            self._outlier_frames = 0
            self._detection_sequence = 0
            self._state = TrackerState.TRACKING
            self._message = f"Tracking {target}"
        return True, f"Tracking {target}"

    def clear_target(self) -> None:
        with self._lock:
            self._target = ""
            self._latest_detection = None
            self._ema_bbox = None
            self._lost_frames = 0
            self._outlier_frames = 0
            self._detection_sequence = 0
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
                max_center_jump = float(
                    self._config.get("yolo_max_center_jump_ratio", 0.12)
                )
                max_area_change = float(
                    self._config.get("yolo_max_area_change_ratio", 1.8)
                )
                outlier_hold_frames = int(
                    self._config.get("yolo_outlier_hold_frames", 4)
                )
                iou_weight = float(self._config.get("yolo_track_iou_weight", 0.5))
            result = model.predict(frame, conf=threshold, verbose=False)[0]
            boxes = result.boxes
            detection: Optional[Detection] = None
            if boxes is not None and len(boxes) > 0:
                confidences = boxes.conf.detach().cpu().numpy()
                xyxy = boxes.xyxy.detach().cpu().numpy()
                candidates = [
                    (
                        float(values[0]),
                        float(values[1]),
                        max(1.0, float(values[2] - values[0])),
                        max(1.0, float(values[3] - values[1])),
                    )
                    for values in xyxy
                ]
                with self._lock:
                    previous = (
                        tuple(float(value) for value in self._ema_bbox)
                        if self._ema_bbox is not None
                        else None
                    )
                    previous_detection = self._latest_detection
                reset_ema = False
                if previous is None:
                    index = int(np.argmax(confidences))
                else:
                    plausible = [
                        candidate_index
                        for candidate_index, candidate in enumerate(candidates)
                        if bbox_center_jump_ratio(
                            previous,
                            candidate,
                            (frame.shape[1], frame.shape[0]),
                        )
                        <= max_center_jump
                        and bbox_area_change_ratio(previous, candidate)
                        <= max_area_change
                    ]
                    if not plausible:
                        if previous_detection is not None:
                            with self._lock:
                                self._outlier_frames += 1
                                if self._outlier_frames <= outlier_hold_frames:
                                    self._detection_sequence += 1
                                    self._state = TrackerState.TRACKING
                                    self._message = (
                                        f"Holding previous {self._target} box after detector jump "
                                        f"({self._outlier_frames}/{outlier_hold_frames})"
                                    )
                                    self._latest_detection = Detection(
                                        bbox=previous_detection.bbox,
                                        confidence=previous_detection.confidence,
                                        trusted=False,
                                        sequence=self._detection_sequence,
                                    )
                                    return
                                self._outlier_frames = 0
                        index = int(np.argmax(confidences))
                        reset_ema = True
                    else:
                        index = max(
                            plausible,
                            key=lambda candidate_index: float(confidences[candidate_index])
                            + iou_weight
                            * bbox_iou(previous, candidates[candidate_index]),
                        )
                raw_bbox = np.array(candidates[index], dtype=float)
                with self._lock:
                    self._outlier_frames = 0
                    self._ema_bbox = (
                        raw_bbox
                        if self._ema_bbox is None or reset_ema
                        else alpha * raw_bbox + (1.0 - alpha) * self._ema_bbox
                    )
                    bbox = tuple(int(value) for value in self._ema_bbox)
                    self._detection_sequence += 1
                    detection = Detection(
                        bbox=bbox,
                        confidence=float(confidences[index]),
                        sequence=self._detection_sequence,
                    )
                    self._latest_detection = detection
                    self._lost_frames = 0
                    self._state = TrackerState.TRACKING
                    self._message = f"Tracking {self._target}"
            else:
                with self._lock:
                    self._lost_frames += 1
                    if self._latest_detection is not None:
                        self._detection_sequence += 1
                        self._latest_detection = Detection(
                            bbox=self._latest_detection.bbox,
                            confidence=self._latest_detection.confidence,
                            trusted=False,
                            sequence=self._detection_sequence,
                        )
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
        self._io_lock = threading.RLock()
        self._last_action_requested: dict[str, float] = {}
        self._last_action_sent: dict[str, float] = {}
        self._torque_enabled = False
        self._torque_off_confirmed = True
        self._torque_fault_message = ""
        self._calibration_state = "IDLE"
        self._calibration_message = "Calibration not started"
        self._calibration_homing_offsets: dict[str, int] = {}
        self._calibration_recorder: CalibrationRangeRecorder | None = None

    @property
    def connected(self) -> bool:
        return self._robot is not None and bool(getattr(self._robot, "is_connected", False))

    @property
    def torque_enabled(self) -> bool:
        return self._torque_enabled

    @property
    def torque_off_confirmed(self) -> bool:
        return self._torque_off_confirmed

    @property
    def torque_fault_message(self) -> str:
        return self._torque_fault_message

    @property
    def calibrated(self) -> bool:
        return self.connected and self._calibration_state == "READY"

    @property
    def calibration_state(self) -> str:
        return self._calibration_state

    @property
    def calibration_message(self) -> str:
        return self._calibration_message

    @property
    def calibration_active(self) -> bool:
        return self._calibration_state in {"WAIT_MIDDLE", "RECORDING_RANGE"}

    def _calibration_mismatch_message(self) -> str:
        if not self.connected or not getattr(self._robot, "calibration", None):
            return "no saved calibration is loaded"
        try:
            actual = self._robot.bus.read_calibration()
        except Exception as exc:
            return f"unable to read motor calibration: {exc}"
        differences = []
        for motor, expected in self._robot.calibration.items():
            observed = actual.get(motor)
            if observed is None:
                differences.append(f"{motor}: missing from motor bus")
                continue
            changed = []
            for field in ("homing_offset", "range_min", "range_max"):
                expected_value = getattr(expected, field)
                observed_value = getattr(observed, field)
                if expected_value != observed_value:
                    changed.append(f"{field} file={expected_value} motor={observed_value}")
            if changed:
                differences.append(f"{motor}: {', '.join(changed)}")
        return "; ".join(differences) or "motor calibration differs for an unknown reason"

    def _calibration_range_issues(self, calibration: dict) -> list[str]:
        issues = []
        for motor, values in calibration.items():
            definition = self._robot.bus.motors.get(motor)
            if definition is None:
                issues.append(f"{motor}: motor is not present")
                continue
            resolution = self._robot.bus.model_resolution_table[definition.model]
            max_position = resolution - 1
            if not 0 <= values.range_min < values.range_max <= max_position:
                issues.append(
                    f"{motor}: saved range {values.range_min}..{values.range_max} "
                    f"is outside 0..{max_position}"
                )
        return issues

    @serialized_arm_io
    def connect(self, port: str, robot_id: str) -> tuple[bool, str]:
        if self.connected:
            return True, "SO-101 is already connected"
        self._torque_fault_message = ""
        self._torque_off_confirmed = False
        try:
            from lerobot.robots.so101_follower.config_so101_follower import SO101FollowerConfig
            from lerobot.robots.so101_follower.so101_follower import SO101Follower

            robot = SO101Follower(SO101FollowerConfig(port=port, id=robot_id))
            self._robot = robot
            robot.connect(calibrate=False)
            torque_safe, torque_message = self.disable_torque()
            if not torque_safe or self._torque_fault_message:
                self._calibration_state = "ERROR"
                self._calibration_message = torque_message
                return False, torque_message
            if not robot.is_calibrated:
                if robot.calibration:
                    range_issues = self._calibration_range_issues(robot.calibration)
                    if range_issues:
                        self._calibration_state = "REQUIRED"
                        self._calibration_message = (
                            "Saved calibration contains wrapped or out-of-range Feetech positions: "
                            f"{'; '.join(range_issues)}. Run calibration once with the updated sampler."
                        )
                        return False, self._calibration_message
                    try:
                        robot.bus.write_calibration(robot.calibration)
                    except Exception as exc:
                        self._calibration_state = "ERROR"
                        self._calibration_message = (
                            f"Saved calibration exists at {robot.calibration_fpath}, but restoring it "
                            f"to the motors failed: {exc}"
                        )
                        return False, self._calibration_message
                    if robot.is_calibrated:
                        self._calibration_state = "READY"
                        self._calibration_message = (
                            f"Saved calibration restored from {robot.calibration_fpath}"
                        )
                        return True, (
                            f"Connected to SO-101 on {port}; saved calibration was restored "
                            "and torque is disabled"
                        )
                    mismatch = self._calibration_mismatch_message()
                    self._calibration_state = "REQUIRED"
                    self._calibration_message = (
                        f"Saved calibration could not be verified after restore: {mismatch}"
                    )
                    return False, self._calibration_message
                self._calibration_state = "REQUIRED"
                self._calibration_message = "No saved SO-101 calibration file was loaded"
                return False, (
                    "SO-101 is connected with torque disabled, but no saved calibration "
                    "file was found"
                )
            self._calibration_state = "READY"
            self._calibration_message = "SO-101 calibration loaded"
            return True, f"Connected to SO-101 on {port} with torque disabled"
        except Exception as exc:
            LOGGER.exception("SO-101 connection failed")
            safety_message = ""
            if self.connected:
                _, safety_message = self.disable_torque()
            else:
                self._robot = None
            self._calibration_state = "ERROR"
            self._calibration_message = f"SO-101 connection failed: {exc}"
            if safety_message:
                self._calibration_message += f"; {safety_message}"
            return False, self._calibration_message

    @serialized_arm_io
    def disconnect(self) -> tuple[bool, str]:
        if not self._robot:
            return True, "SO-101 is already disconnected"
        torque_success, torque_message = self.disable_torque()
        close_errors = []
        try:
            bus = self._robot.bus
            if bool(getattr(bus, "is_connected", False)):
                try:
                    bus.disconnect(disable_torque=False)
                except Exception as exc:
                    close_errors.append(str(exc))
                    port_handler = getattr(bus, "port_handler", None)
                    if port_handler is not None:
                        try:
                            port_handler.closePort()
                        except Exception as close_exc:
                            close_errors.append(str(close_exc))
            for camera in getattr(self._robot, "cameras", {}).values():
                if bool(getattr(camera, "is_connected", False)):
                    camera.disconnect()
        except Exception as exc:
            close_errors.append(str(exc))
        finally:
            self._robot = None
            self._torque_enabled = False
            if torque_success:
                self._torque_off_confirmed = True
            if not self.calibration_active:
                self._calibration_state = "IDLE"
                self._calibration_message = "SO-101 disconnected"
        if close_errors:
            return False, (
                f"SO-101 serial close failed: {'; '.join(close_errors)}. "
                "Physically power off the arm before unplugging USB."
            )
        if not torque_success:
            return False, f"SO-101 serial port closed; {torque_message}"
        if self._torque_fault_message:
            return True, f"SO-101 disconnected; {self._torque_fault_message}"
        return True, "SO-101 disconnected with torque disabled"

    @serialized_arm_io
    def start_calibration(self, port: str, robot_id: str) -> tuple[bool, str]:
        try:
            from lerobot.motors.feetech import OperatingMode
            from lerobot.robots.so101_follower.config_so101_follower import SO101FollowerConfig
            from lerobot.robots.so101_follower.so101_follower import SO101Follower

            if self.connected:
                robot = self._robot
            else:
                last_error = None
                for attempt in range(3):
                    robot = SO101Follower(SO101FollowerConfig(port=port, id=robot_id))
                    self._robot = robot
                    try:
                        robot.connect(calibrate=False)
                        break
                    except Exception as exc:
                        last_error = exc
                        if self.connected:
                            break
                        self._robot = None
                        if attempt < 2:
                            time.sleep(0.4)
                else:
                    raise last_error
                if not self.connected:
                    raise last_error
            torque_safe, torque_message = self.disable_torque()
            if not torque_safe or self._torque_fault_message:
                self._calibration_state = "ERROR"
                self._calibration_message = (
                    "Calibration refused because torque-off is not clean: "
                    f"{torque_message}. Power off the arm and let all motors cool before retrying."
                )
                return False, self._calibration_message
            for motor in robot.bus.motors:
                robot.bus.write(
                    "Operating_Mode",
                    motor,
                    OperatingMode.POSITION.value,
                    num_retry=2,
                )
            self._torque_enabled = False
            self._torque_off_confirmed = True
            self._calibration_homing_offsets = {}
            self._calibration_recorder = None
            self._calibration_state = "WAIT_MIDDLE"
            self._calibration_message = (
                "Move every joint to the middle of its usable range, then record middle"
            )
            return True, self._calibration_message
        except Exception as exc:
            LOGGER.exception("SO-101 calibration start failed")
            if self.connected:
                self.disable_torque()
            else:
                self._robot = None
            self._calibration_state = "ERROR"
            self._calibration_message = (
                f"Calibration start failed: {exc}. If the port was just released, wait one second "
                "and retry. If any motor reported overheat, physically power off and let it cool first."
            )
            return False, self._calibration_message

    @serialized_arm_io
    def calibration_set_middle(self) -> tuple[bool, str]:
        if self._calibration_state != "WAIT_MIDDLE" or not self.connected:
            return False, "Calibration is not waiting for the middle position"
        try:
            self._calibration_homing_offsets = self._robot.bus.set_half_turn_homings()
            positions = {
                motor: decode_feetech_position(position)
                for motor, position in self._robot.bus.sync_read(
                    "Present_Position", normalize=False
                ).items()
            }
            self._calibration_recorder = CalibrationRangeRecorder(positions)
            self._calibration_state = "RECORDING_RANGE"
            self._calibration_message = (
                "Move every joint, including the gripper, through its full safe range"
            )
            return True, self._calibration_message
        except Exception as exc:
            self._calibration_state = "ERROR"
            self._calibration_message = f"Middle-position calibration failed: {exc}"
            return False, self._calibration_message

    def calibration_sample(self) -> None:
        if self._calibration_state != "RECORDING_RANGE" or not self.connected:
            return
        if not self._io_lock.acquire(blocking=False):
            return
        try:
            positions = {
                motor: decode_feetech_position(position)
                for motor, position in self._robot.bus.sync_read(
                    "Present_Position", normalize=False
                ).items()
            }
            self._calibration_recorder.update(positions)
        except Exception as exc:
            self._calibration_state = "ERROR"
            self._calibration_message = f"Range recording failed: {exc}"
        finally:
            self._io_lock.release()

    @serialized_arm_io
    def finish_calibration(self) -> tuple[bool, str]:
        if self._calibration_state != "RECORDING_RANGE" or not self.connected:
            return False, "Calibration is not recording joint ranges"
        try:
            from lerobot.motors import MotorCalibration

            range_mins, range_maxes = self._calibration_recorder.result()
            calibration = {}
            for motor, definition in self._robot.bus.motors.items():
                resolution = self._robot.bus.model_resolution_table[definition.model]
                homing_offset, range_min, range_max = recenter_feetech_calibration_range(
                    self._calibration_homing_offsets[motor],
                    range_mins[motor],
                    range_maxes[motor],
                    resolution,
                )
                calibration[motor] = MotorCalibration(
                    id=definition.id,
                    drive_mode=0,
                    homing_offset=homing_offset,
                    range_min=range_min,
                    range_max=range_max,
                )
            range_issues = self._calibration_range_issues(calibration)
            if range_issues:
                raise ValueError(f"Invalid calibration ranges: {'; '.join(range_issues)}")
            self._robot.calibration = calibration
            self._robot.bus.write_calibration(calibration)
            self._robot.configure()
            torque_safe, torque_message = self.disable_torque()
            if not torque_safe or self._torque_fault_message:
                raise RuntimeError(
                    f"Calibration was written, but torque-off after configuration failed: {torque_message}"
                )
            if not self._robot.is_calibrated:
                raise RuntimeError(
                    "Calibration read-back did not match: "
                    f"{self._calibration_mismatch_message()}"
                )
            self._robot._save_calibration()
            self._calibration_state = "READY"
            self._calibration_message = (
                f"Calibration saved to {self._robot.calibration_fpath}"
            )
            return True, self._calibration_message
        except Exception as exc:
            self._calibration_state = "ERROR"
            self._calibration_message = f"Calibration finish failed: {exc}"
            return False, self._calibration_message

    @serialized_arm_io
    def cancel_calibration(self) -> tuple[bool, str]:
        if self._robot is not None:
            self.disconnect()
        self._calibration_homing_offsets = {}
        self._calibration_recorder = None
        self._calibration_state = "IDLE"
        self._calibration_message = "Calibration canceled"
        return True, self._calibration_message

    @serialized_arm_io
    def enable_torque(self) -> tuple[bool, str]:
        if not self.connected:
            return False, "SO-101 is not connected"
        if self.calibration_active:
            return False, "Torque cannot be enabled during calibration"
        if self._torque_fault_message:
            return False, (
                "Torque cannot be enabled while a motor hardware fault is latched. "
                f"{self._torque_fault_message}"
            )
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
        self._torque_off_confirmed = False
        return True, "SO-101 torque enabled"

    @serialized_arm_io
    def disable_torque(self) -> tuple[bool, str]:
        if not self.connected:
            self._torque_enabled = False
            self._torque_off_confirmed = True
            return True, "SO-101 is not connected"
        bus = self._robot.bus
        try:
            bus.disable_torque(num_retry=2)
            self._torque_enabled = False
            self._torque_off_confirmed = True
            self._torque_fault_message = ""
            return True, "SO-101 torque disabled"
        except Exception as first_error:
            LOGGER.warning("Full torque disable failed: %s", first_error)
            first_error_text = str(first_error)

        if "Port is in use" in first_error_text:
            port_handler = getattr(bus, "port_handler", None)
            if port_handler is not None and bool(getattr(port_handler, "is_using", False)):
                port_handler.is_using = False
                time.sleep(0.02)
                try:
                    bus.disable_torque(num_retry=2)
                    self._torque_enabled = False
                    self._torque_off_confirmed = True
                    self._torque_fault_message = ""
                    return True, "SO-101 torque disabled after clearing a stale port-busy flag"
                except Exception as retry_error:
                    LOGGER.warning("Torque disable retry after port reset failed: %s", retry_error)

        failures = {}
        for motor, definition in bus.motors.items():
            try:
                bus.disable_torque(motors=motor, num_retry=2)
            except Exception as exc:
                failures[f"{motor}(id={definition.id})"] = str(exc)

        if not failures:
            self._torque_enabled = False
            self._torque_off_confirmed = True
            self._torque_fault_message = ""
            return True, "SO-101 torque disabled individually after group retry"

        all_motors_off = dict.fromkeys(bus.motors, 0)
        try:
            bus.sync_write(
                "Torque_Enable",
                all_motors_off,
                normalize=False,
                num_retry=3,
            )
            broadcast_sent = True
        except Exception as exc:
            broadcast_sent = False
            broadcast_error = str(exc)

        verified = False
        enabled_motors = []
        verification_error = ""
        if broadcast_sent:
            try:
                torque_states = bus.sync_read(
                    "Torque_Enable",
                    normalize=False,
                    num_retry=2,
                )
                enabled_motors = [
                    motor for motor, value in torque_states.items() if int(value) != 0
                ]
                verified = not enabled_motors
            except Exception as exc:
                verification_error = str(exc)

        temperature_text = ""
        try:
            temperatures = bus.sync_read(
                "Present_Temperature",
                normalize=False,
                num_retry=1,
            )
            temperature_text = "; temperatures=" + ", ".join(
                f"{motor}:{int(value)}C" for motor, value in temperatures.items()
            )
        except Exception:
            pass

        fault_details = "; ".join(
            f"{motor}: {error}" for motor, error in failures.items()
        )
        self._torque_fault_message = (
            f"Motor hardware faults were reported while disabling torque: {fault_details}"
            f"{temperature_text}. Physically power off the arm and let the motors cool before reconnecting."
        )
        self._torque_enabled = bool(enabled_motors)
        self._torque_off_confirmed = verified
        if verified:
            self._torque_enabled = False
            return True, (
                "SO-101 torque-off was broadcast and verified, but hardware faults remain. "
                f"{self._torque_fault_message}"
            )
        if broadcast_sent:
            detail = verification_error or f"still enabled: {enabled_motors}"
            return False, (
                "EMERGENCY torque-off broadcast was sent but could not be verified "
                f"({detail}). {self._torque_fault_message}"
            )
        return False, (
            "SO-101 torque disable failed, including the emergency broadcast "
            f"({broadcast_error}). {self._torque_fault_message}"
        )

    def get_joints(self) -> dict[str, float]:
        if not self.connected or self.calibration_active:
            return {}
        if not self._io_lock.acquire(blocking=False):
            return {}
        try:
            observation = self._robot.get_observation()
            return {name: float(observation[name]) for name in ALL_JOINTS if name in observation}
        except Exception as exc:
            LOGGER.warning("Unable to read SO-101 joints: %s", exc)
            return {}
        finally:
            self._io_lock.release()

    @serialized_arm_io
    def diagnostic_snapshot(self) -> dict[str, Any]:
        snapshot: dict[str, Any] = {
            "connected": self.connected,
            "torque_enabled": self.torque_enabled,
            "torque_off_confirmed": self.torque_off_confirmed,
            "calibration_state": self.calibration_state,
            "calibration_message": self.calibration_message,
            "torque_fault_message": self.torque_fault_message,
            "last_action_requested": dict(self._last_action_requested),
            "last_action_sent": dict(self._last_action_sent),
        }
        if self._robot is None:
            return snapshot
        snapshot["calibration_path"] = str(
            getattr(self._robot, "calibration_fpath", "")
        )
        snapshot["calibration"] = {
            name: {
                field: getattr(values, field, None)
                for field in (
                    "id",
                    "drive_mode",
                    "homing_offset",
                    "range_min",
                    "range_max",
                )
            }
            for name, values in getattr(self._robot, "calibration", {}).items()
        }
        bus = getattr(self._robot, "bus", None)
        snapshot["motors"] = {
            name: {
                "id": getattr(motor, "id", None),
                "model": getattr(motor, "model", None),
                "norm_mode": str(getattr(motor, "norm_mode", "")),
            }
            for name, motor in getattr(bus, "motors", {}).items()
        }
        registers: dict[str, Any] = {}
        if bus is not None and bool(getattr(bus, "is_connected", False)):
            for register in (
                "Present_Position",
                "Goal_Position",
                "Present_Velocity",
                "Present_Load",
                "Present_Current",
                "Present_Temperature",
                "Moving",
                "Status",
                "Torque_Enable",
            ):
                try:
                    registers[register] = bus.sync_read(
                        register,
                        normalize=False,
                        num_retry=0,
                    )
                except Exception as exc:
                    registers[register] = {"error": str(exc)}
        snapshot["raw_registers"] = registers
        return snapshot

    @serialized_arm_io
    def send_arm_joints(self, desired: dict[str, float]) -> tuple[bool, str]:
        if not self.connected:
            return False, "SO-101 is not connected"
        if self.calibration_active:
            return False, "Arm commands are disabled during calibration"
        missing = [name for name in ARM_JOINTS if name not in desired]
        current: dict[str, float] = {}
        if missing:
            current = self.get_joints()
            if not current:
                return False, (
                    "Unable to read SO-101 joint positions required to complete a partial command"
                )
        action = {
            name: float(desired[name] if name in desired else current[name])
            for name in ARM_JOINTS
        }
        try:
            self._last_action_requested = dict(action)
            sent = self._robot.send_action(action)
            self._last_action_sent = {
                name: float(value) for name, value in dict(sent).items()
            }
            return True, "Arm command sent"
        except Exception as exc:
            return False, f"Arm command failed: {exc}"

    @serialized_arm_io
    def set_gripper(self, position: float) -> tuple[bool, str]:
        if not self.connected:
            return False, "SO-101 is not connected"
        if self.calibration_active:
            return False, "Gripper commands are disabled during calibration"
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
        self._move_start: Optional[dict[str, float]] = None
        self._move_completion_state = ServoState.IDLE
        self._move_name = ""
        self._move_started = 0.0
        self._move_duration = 0.0
        self._move_settle_cycles = 0
        self._move_near_settle_cycles = 0
        self._move_last_feedback: Optional[dict[str, float]] = None
        self._grasp_started = 0.0
        self._centering_limit_cycles = 0
        self._centering_error_history: deque[tuple[float, float]] = deque(maxlen=15)
        self._centering_confirm_cycles = 0
        self._last_centering_command = 0.0
        self._last_visual_detection_sequence = 0
        self._session_target_center_y: Optional[float] = None
        self._centering_command_target: Optional[dict[str, float]] = None
        self._approach_profile_start: Optional[dict[str, float]] = None
        self._approach_command_target: Optional[dict[str, float]] = None
        self._final_approach_started = 0.0
        self._final_approach_start_lift: Optional[float] = None
        self._final_approach_command_target: Optional[dict[str, float]] = None
        self._last_final_approach_command = 0.0
        self._final_approach_endpoint_reached_at = 0.0
        self._debug_callback: Optional[
            Callable[[str, dict[str, Any]], None]
        ] = None
        self.demo_recording = False
        self.demo_rows: list[dict[str, Any]] = []
        self._demo_started_monotonic = 0.0
        self._demo_target = ""

    def update_config(self, config: dict[str, Any]) -> None:
        self.config.update(config)

    def start_demo_recording(self, target: str) -> None:
        self.demo_rows.clear()
        self.demo_recording = True
        self._demo_started_monotonic = time.monotonic()
        self._demo_target = target.strip()

    def stop_demo_recording(self) -> list[dict[str, Any]]:
        self.demo_recording = False
        return list(self.demo_rows)

    def visual_target_center(self, frame_size: tuple[int, int]) -> tuple[float, float]:
        width, height = frame_size
        target_x = width / 2.0 + float(self.config["center_offset_x"])
        target_y = (
            self._session_target_center_y
            if self._session_target_center_y is not None
            else height / 2.0 + float(self.config["center_offset_y"])
        )
        return target_x, target_y

    def use_configured_visual_center(self) -> None:
        self._session_target_center_y = None
        self._centering_error_history.clear()
        self._centering_confirm_cycles = 0

    def set_debug_callback(
        self,
        callback: Optional[Callable[[str, dict[str, Any]], None]],
    ) -> None:
        self._debug_callback = callback

    def _debug(self, event: str, **payload: Any) -> None:
        if self._debug_callback is None:
            return
        try:
            self._debug_callback(event, payload)
        except Exception:
            LOGGER.exception("Visual grasp debug callback failed")

    def set_tracking(self) -> None:
        if self.state not in (
            ServoState.CENTERING,
            ServoState.APPROACHING,
            ServoState.FINAL_APPROACH,
            ServoState.MOVING,
        ):
            self.state = ServoState.TRACKING
            self.message = "Tracking; waiting for manual grasp command"

    def _reset_centering_filter(self, reset_detection_sequence: bool = True) -> None:
        self._centering_error_history.clear()
        self._centering_confirm_cycles = 0
        self._last_centering_command = 0.0
        self._centering_limit_cycles = 0
        self._centering_command_target = None
        self._approach_profile_start = None
        self._approach_command_target = None
        self._final_approach_started = 0.0
        self._final_approach_start_lift = None
        self._final_approach_command_target = None
        self._last_final_approach_command = 0.0
        self._final_approach_endpoint_reached_at = 0.0
        if reset_detection_sequence:
            self._last_visual_detection_sequence = 0
            self._session_target_center_y = None

    def start_approach(self) -> tuple[bool, str]:
        accepted, message = self.validate_grasp_start()
        if not accepted:
            return False, message
        self._reset_centering_filter()
        self.state = ServoState.CENTERING
        self._grasp_started = time.monotonic()
        self.message = "Centering target"
        return True, self.message

    def start_grasp_sequence(self) -> tuple[bool, str]:
        accepted, message = self.validate_grasp_start()
        if not accepted:
            return False, message
        if "pregrasp" not in self.positions:
            return False, "Record the pregrasp position before automatic grasping"
        limit = float(self.config.get("joint_command_limit", 95.0))
        pregrasp = self.positions["pregrasp"]
        outside = {
            name: float(pregrasp[name])
            for name in (
                "shoulder_pan.pos",
                "shoulder_lift.pos",
                "elbow_flex.pos",
            )
            if name in pregrasp and abs(float(pregrasp[name])) > limit
        }
        if outside:
            return False, (
                f"Pregrasp pose is outside visual-servo soft limit +/-{limit:.1f}: "
                f"{outside}; re-record pregrasp or increase joint_command_limit carefully"
            )
        self._grasp_started = 0.0
        self._reset_centering_filter()
        return self.go_to_position("pregrasp", completion_state=ServoState.CENTERING)

    def validate_grasp_start(self) -> tuple[bool, str]:
        if not self.arm.connected:
            return False, "SO-101 is not connected"
        if not self.arm.torque_enabled:
            return False, "Enable SO-101 torque before grasping"
        if bool(self.config.get("tof_control_enabled", False)) and not bool(
            self.config.get("tof_enabled", False)
        ):
            return False, "ToF control requires tof_enabled=true"
        if bool(self.config.get("tof_control_enabled", False)) and not bool(
            self.config.get("tof_calibrated", False)
        ):
            return False, "ToF control requires tof_calibrated=true"
        if bool(self.config.get("visual_handoff_enabled", True)):
            missing = []
            if not bool(self.config.get("tof_enabled", False)):
                missing.append("tof_enabled")
            if not bool(self.config.get("tof_control_enabled", False)):
                missing.append("tof_control_enabled")
            if not bool(self.config.get("tof_calibrated", False)):
                missing.append("tof_calibrated")
            if missing:
                return False, (
                    "近距离视觉交接已启用，请先开启并确认这些 ToF 选项："
                    + ", ".join(missing)
                )
        if self.state in (
            ServoState.MOVING,
            ServoState.CENTERING,
            ServoState.APPROACHING,
            ServoState.FINAL_APPROACH,
            ServoState.RANGE_WAIT,
        ):
            return False, "Stop the current arm motion before starting a new grasp"
        return True, "Grasp start conditions are ready"

    def _hold_current_position(self) -> tuple[bool, str]:
        if not self.arm.connected or not self.arm.torque_enabled:
            return True, "Arm hold is unnecessary while disconnected or torque is disabled"
        joints = self.arm.get_joints()
        if not joints:
            return False, "Unable to read current joints for an immediate hold command"
        desired = {name: float(joints[name]) for name in ARM_JOINTS}
        ok, message = self.arm.send_arm_joints(desired)
        self._debug(
            "motion_hold_requested",
            success=ok,
            desired=desired,
            message=message,
        )
        return ok, message

    def stop(
        self,
        keep_tracking: bool = False,
        hold_position: bool = True,
    ) -> tuple[bool, str]:
        hold_ok = True
        hold_message = ""
        if hold_position:
            hold_ok, hold_message = self._hold_current_position()
        self._move_target = None
        self._move_start = None
        self._move_completion_state = ServoState.IDLE
        self._move_name = ""
        self._move_duration = 0.0
        self._move_settle_cycles = 0
        self._move_near_settle_cycles = 0
        self._move_last_feedback = None
        self._grasp_started = 0.0
        self._reset_centering_filter()
        self.state = ServoState.TRACKING if keep_tracking else ServoState.IDLE
        self.message = (
            "Motion stopped; detection tracking remains active"
            if keep_tracking
            else "Motion and visual-servo control stopped"
        )
        if hold_position and not hold_ok:
            self.message += f"; WARNING: {hold_message}. Disable torque or cut power now"
        elif hold_position and self.arm.connected and self.arm.torque_enabled:
            self.message += "; current joint position is being held"
        return hold_ok, self.message

    def update(
        self,
        detection: Optional[Detection],
        frame_size: tuple[int, int],
        tof_reading: Optional[TofReading] = None,
    ) -> None:
        if self.demo_recording:
            self._record_demo(detection, frame_size, tof_reading)
            return
        if self.state == ServoState.MOVING:
            self._tick_move()
            return
        if self.state not in (
            ServoState.CENTERING,
            ServoState.APPROACHING,
            ServoState.FINAL_APPROACH,
            ServoState.RANGE_WAIT,
        ):
            return
        if self._grasp_started <= 0.0:
            self._grasp_started = time.monotonic()
        if time.monotonic() - self._grasp_started > float(
            self.config.get("grasp_timeout_sec", 20.0)
        ):
            self.state = ServoState.ERROR
            self.message = "Visual grasp timed out before completion"
            return
        if self.state == ServoState.FINAL_APPROACH:
            self._tick_final_approach(tof_reading)
            return
        if detection is None:
            self.message = "Target lost while grasping"
            return
        if not detection.trusted:
            self._centering_confirm_cycles = 0
            self.message = "Detector jump rejected; holding visual-servo motion"
            self._debug(
                "visual_servo_detection_held",
                state=self.state.value,
                bbox=detection.bbox,
                confidence=detection.confidence,
            )
            return
        if (
            detection.sequence > 0
            and detection.sequence <= self._last_visual_detection_sequence
        ):
            return
        if detection.sequence > 0:
            self._last_visual_detection_sequence = detection.sequence
        if self.state == ServoState.CENTERING:
            self._tick_center(detection, frame_size)
        elif self.state in (ServoState.APPROACHING, ServoState.RANGE_WAIT):
            self._tick_approach(detection, frame_size, tof_reading)

    def record_position(self, name: str) -> tuple[bool, str]:
        joints = self.arm.get_joints()
        if not joints:
            return False, "Unable to read SO-101 joints"
        accepted, message = self._validate_preset_pose(joints, name)
        if not accepted:
            return False, message
        self.positions[name] = joints
        self._debug("preset_recorded", preset=name, joints=dict(joints))
        suffix = f"; {message}" if message.startswith("Warning:") else ""
        return True, f"Recorded {name} position{suffix}"

    def go_to_position(
        self,
        name: str,
        completion_state: ServoState = ServoState.IDLE,
    ) -> tuple[bool, str]:
        target = self.positions.get(name)
        if not target:
            return False, f"No saved {name} position"
        accepted, message = self._validate_preset_pose(target, name)
        if not accepted:
            return False, message
        if not self.arm.connected or not self.arm.torque_enabled:
            return False, "Connect arm and enable torque first"
        if self.state in (
            ServoState.MOVING,
            ServoState.CENTERING,
            ServoState.APPROACHING,
            ServoState.FINAL_APPROACH,
            ServoState.RANGE_WAIT,
        ):
            return False, "Stop the current arm motion before moving to a preset"
        current = self.arm.get_joints()
        if not current:
            return False, "Unable to read joints before preset move"
        move_target = {
            name: float(target.get(name, current[name])) for name in ARM_JOINTS
        }
        invalid = {
            name: value for name, value in move_target.items() if not -100.0 <= value <= 100.0
        }
        if invalid:
            return False, f"Preset contains invalid normalized joint values: {invalid}"
        maximum_delta = max(
            abs(move_target[name] - float(current[name])) for name in ARM_JOINTS
        )
        command_rate = max(
            1.0,
            float(self.config.get("move_step_limit", 3.0))
            * float(self.config.get("move_fps", 15.0)),
        )
        duration = max(0.75, 1.5 * maximum_delta / command_rate)
        timeout = float(self.config.get("move_timeout_sec", 15.0))
        if duration + 2.0 > timeout:
            return False, (
                f"Preset requires about {duration:.1f}s but move_timeout_sec is {timeout:.1f}s; "
                "increase the timeout or move_step_limit"
            )
        self._move_start = {name: float(current[name]) for name in ARM_JOINTS}
        self._move_target = move_target
        self._move_completion_state = completion_state
        self._move_name = name
        self._move_started = time.monotonic()
        self._move_duration = duration
        self._move_settle_cycles = 0
        self._move_near_settle_cycles = 0
        self._move_last_feedback = None
        self.state = ServoState.MOVING
        self.message = f"Moving all joints smoothly to {name} in {duration:.1f}s"
        self._debug(
            "preset_move_started",
            preset=name,
            start=dict(self._move_start),
            target=dict(self._move_target),
            duration_sec=duration,
            timeout_sec=timeout,
            arrive_threshold=float(self.config.get("arrive_threshold", 2.0)),
            elbow_arrive_threshold=float(
                self.config.get(
                    "elbow_arrive_threshold",
                    self.config.get("arrive_threshold", 2.0),
                )
            ),
            stable_margin=float(self.config.get("arrive_stable_margin", 0.75)),
            stable_delta=float(self.config.get("arrive_stable_delta", 0.35)),
            stable_cycles=int(self.config.get("arrive_stable_cycles", 5)),
        )
        return True, self.message

    def _validate_preset_pose(
        self,
        pose: dict[str, float],
        name: str,
    ) -> tuple[bool, str]:
        operational_limit = float(
            self.config.get(
                "preset_joint_limit",
                self.config.get("joint_command_limit", 95.0),
            )
        )
        limit = (
            float(self.config.get("standby_joint_limit", 99.5))
            if name == "standby"
            else operational_limit
        )
        outside = {
            joint_name: float(pose[joint_name])
            for joint_name in ARM_JOINTS
            if joint_name in pose and abs(float(pose[joint_name])) > limit
        }
        if outside:
            return False, (
                f"Preset {name} is too close to the calibrated joint endpoint "
                f"(+/-{limit:.1f}): {outside}. Move those joints inward with torque off "
                "and record the preset again"
            )
        extended = {
            joint_name: float(pose[joint_name])
            for joint_name in ARM_JOINTS
            if name == "standby"
            and joint_name in pose
            and abs(float(pose[joint_name])) > operational_limit
        }
        if extended:
            return True, (
                "Warning: standby uses the extended supervised endpoint allowance "
                f"(+/-{limit:.1f}): {extended}"
            )
        return True, "Preset is inside the configured joint endpoint margin"

    def _tick_center(self, detection: Detection, frame_size: tuple[int, int]) -> None:
        width, height = frame_size
        x, y, box_width, box_height = detection.bbox
        center_x = x + box_width / 2.0
        center_y = y + box_height / 2.0
        if (
            self._session_target_center_y is None
            and bool(self.config.get("auto_lock_vertical_center_on_pregrasp", False))
        ):
            lower_offset_ratio = max(
                -0.25,
                min(
                    0.40,
                    float(
                        self.config.get(
                            "auto_lock_vertical_center_offset_ratio",
                            0.10,
                        )
                    ),
                ),
            )
            self._session_target_center_y = max(
                0.0,
                min(
                    float(height - 1),
                    center_y + box_height * lower_offset_ratio,
                ),
            )
            self._centering_error_history.clear()
            self._centering_confirm_cycles = 0
            self._debug(
                "vertical_center_auto_locked",
                bbox_center_y=center_y,
                target_center_y=self._session_target_center_y,
                lower_offset_ratio=lower_offset_ratio,
                frame_height=height,
                previous_configured_center_y=(
                    height / 2.0 + float(self.config["center_offset_y"])
                ),
            )
        target_center_x, target_center_y = self.visual_target_center(frame_size)
        error_x = (center_x - target_center_x) / width
        error_y = (center_y - target_center_y) / height
        self._centering_error_history.append((error_x, error_y))
        error_window = max(1, int(self.config.get("centering_error_window", 3)))
        samples = list(self._centering_error_history)[-error_window:]
        minimum_samples = max(1, int(self.config.get("centering_min_samples", 2)))
        filtered_error_x = median(sample[0] for sample in samples)
        filtered_error_y = median(sample[1] for sample in samples)
        if len(samples) < minimum_samples:
            self.message = (
                f"Waiting for stable target samples ({len(samples)}/{minimum_samples})"
            )
            return
        threshold = float(self.config["centering_threshold"])
        tilt_motion_enabled = bool(
            self.config.get("centering_tilt_motion_enabled", False)
        )
        horizontal_centered = abs(filtered_error_x) <= threshold
        vertical_centered = abs(filtered_error_y) <= threshold
        centered = horizontal_centered and (
            vertical_centered if tilt_motion_enabled else True
        )
        self._centering_confirm_cycles = (
            self._centering_confirm_cycles + 1 if centered else 0
        )
        confirm_cycles = max(1, int(self.config.get("centering_confirm_cycles", 2)))
        if self._centering_confirm_cycles >= confirm_cycles:
            self._reset_centering_filter(reset_detection_sequence=False)
            self.state = ServoState.APPROACHING
            self.message = (
                "Target centered consistently; starting taught horizontal approach"
                if tilt_motion_enabled
                else "Horizontal target centered; starting taught horizontal approach"
            )
            return
        if centered:
            self.message = (
                f"Confirming centered target ({self._centering_confirm_cycles}/{confirm_cycles})"
            )
            return
        now = time.monotonic()
        command_interval = max(
            0.0,
            float(self.config.get("centering_command_interval_sec", 0.08)),
        )
        if now - self._last_centering_command < command_interval:
            self.message = "Filtering target jitter before next centering correction"
            return
        joints = self.arm.get_joints()
        if not joints:
            self.state = ServoState.ERROR
            self.message = "Unable to read joints for centering"
            return
        desired = dict(joints)
        maximum_step = float(self.config.get("centering_step_limit", 1.5))
        minimum_step = min(
            maximum_step,
            max(0.0, float(self.config.get("centering_min_step_limit", 0.25))),
        )
        slow_zone = max(
            float(self.config["centering_threshold"]),
            float(self.config.get("centering_slow_zone", 0.12)),
        )

        def adaptive_delta(error: float, gain: float, direction: float) -> float:
            absolute_error = abs(error)
            threshold = float(self.config["centering_threshold"])
            if absolute_error <= threshold:
                return 0.0
            if slow_zone <= threshold:
                allowed_step = maximum_step
            else:
                progress = min(
                    1.0,
                    max(0.0, (absolute_error - threshold) / (slow_zone - threshold)),
                )
                allowed_step = minimum_step + (maximum_step - minimum_step) * progress
            requested_delta = direction * gain * error
            return max(-allowed_step, min(allowed_step, requested_delta))

        pan_delta = adaptive_delta(
            filtered_error_x,
            float(self.config["pan_gain"]),
            float(self.config.get("pan_direction", 1.0)),
        )
        lift_delta = (
            adaptive_delta(
                filtered_error_y,
                float(self.config["tilt_gain"]),
                float(self.config.get("tilt_direction", -1.0)),
            )
            if tilt_motion_enabled
            else 0.0
        )
        vertical_alignment_required = not tilt_motion_enabled and not vertical_centered
        maximum_command_lead = max(
            maximum_step,
            float(self.config.get("centering_max_command_lead", 4.0)),
        )
        previous_target = self._centering_command_target or dict(joints)

        def accumulated_target(joint_name: str, delta: float) -> float:
            feedback = float(joints[joint_name])
            previous = float(previous_target.get(joint_name, feedback))
            previous_lead = previous - feedback
            if delta == 0.0:
                return feedback
            if previous_lead * delta < 0.0:
                previous = feedback
            requested_target = previous + delta
            return max(
                feedback - maximum_command_lead,
                min(feedback + maximum_command_lead, requested_target),
            )

        desired["shoulder_pan.pos"] = accumulated_target(
            "shoulder_pan.pos", pan_delta
        )
        desired["shoulder_lift.pos"] = accumulated_target(
            "shoulder_lift.pos", lift_delta
        )
        limit = float(self.config.get("joint_command_limit", 95.0))
        requested = {
            name: float(desired[name])
            for name in ("shoulder_pan.pos", "shoulder_lift.pos")
        }
        clamped = {}
        for name, value in requested.items():
            bounded = max(-limit, min(limit, value))
            desired[name] = bounded
            if bounded != value:
                clamped[name] = {
                    "requested": value,
                    "sent": bounded,
                }
        self._centering_limit_cycles = (
            self._centering_limit_cycles + 1 if clamped else 0
        )
        self._debug(
            "centering_tick",
            bbox=detection.bbox,
            frame_size=frame_size,
            raw_error_x=error_x,
            raw_error_y=error_y,
            filtered_error_x=filtered_error_x,
            filtered_error_y=filtered_error_y,
            error_samples=samples,
            detection_sequence=detection.sequence,
            pan_delta=pan_delta,
            lift_delta=lift_delta,
            previous_command_target={
                name: float(previous_target.get(name, joints[name]))
                for name in ("shoulder_pan.pos", "shoulder_lift.pos")
            },
            maximum_command_lead=maximum_command_lead,
            joints=dict(joints),
            requested=requested,
            desired={name: float(desired[name]) for name in ARM_JOINTS},
            clamped=clamped,
            soft_limit=limit,
            limit_cycles=self._centering_limit_cycles,
        )
        limit_hold_cycles = int(
            self.config.get("centering_limit_hold_cycles", 3)
        )
        if clamped and self._centering_limit_cycles >= limit_hold_cycles:
            self.state = ServoState.ERROR
            self.message = (
                f"Centering could not converge before visual-servo soft limit +/-{limit:.1f}: "
                f"{clamped}; re-record pregrasp farther from the limit or adjust center_offset_y"
            )
            return
        ok, message = self.arm.send_arm_joints(desired)
        if not ok:
            self.state = ServoState.ERROR
            self.message = message
            return
        self._centering_command_target = {
            name: float(desired[name])
            for name in ("shoulder_pan.pos", "shoulder_lift.pos")
        }
        self._last_centering_command = now
        if clamped:
            self.message = (
                f"Centering correction clamped safely at +/-{limit:.1f}; "
                f"waiting for target response ({self._centering_limit_cycles}/{limit_hold_cycles})"
            )
        elif vertical_alignment_required:
            self.message = (
                "Centering horizontally; the lower yellow grasp point is a guide for "
                "the taught approach profile"
            )

    def _taught_approach_target(
        self,
        joints: dict[str, float],
        requested_lift: float,
    ) -> tuple[dict[str, float], float, float]:
        if self._approach_profile_start is None:
            self._approach_profile_start = {
                name: float(joints[name])
                for name in (
                    "shoulder_lift.pos",
                    "elbow_flex.pos",
                    "wrist_flex.pos",
                )
            }
            self._debug(
                "approach_profile_started",
                start=dict(self._approach_profile_start),
                source="visual_demo_20260810_190054",
            )
        start = self._approach_profile_start
        taught_lift_delta = max(
            0.1,
            float(self.config.get("approach_profile_max_lift_delta", 34.0)),
        )
        start_lift = float(start["shoulder_lift.pos"])
        saved_pregrasp_lift = float(
            self.positions.get("pregrasp", {}).get(
                "shoulder_lift.pos",
                start_lift,
            )
        )
        end_lift = saved_pregrasp_lift + taught_lift_delta
        maximum_lift_delta = max(0.1, end_lift - start_lift)
        lift = max(
            start_lift,
            min(end_lift, requested_lift),
        )
        progress = max(0.0, min(1.0, (lift - start_lift) / maximum_lift_delta))
        desired = dict(joints)
        desired["shoulder_lift.pos"] = lift
        desired["elbow_flex.pos"] = float(start["elbow_flex.pos"]) + progress * float(
            self.config.get("approach_profile_elbow_delta", 12.3)
        )
        wrist_trim = max(
            -10.0,
            min(
                10.0,
                float(self.config.get("approach_profile_wrist_trim", 0.0)),
            ),
        )
        effective_wrist_delta = float(
            self.config.get("approach_profile_wrist_delta", -54.0)
        ) + wrist_trim
        desired["wrist_flex.pos"] = (
            float(start["wrist_flex.pos"]) + progress * effective_wrist_delta
        )
        self._debug(
            "approach_profile_target",
            actual_start_lift=start_lift,
            saved_pregrasp_lift=saved_pregrasp_lift,
            taught_lift_delta=taught_lift_delta,
            end_lift=end_lift,
            requested_lift=requested_lift,
            sent_lift=lift,
            progress=progress,
            wrist_trim=wrist_trim,
            effective_wrist_delta=effective_wrist_delta,
        )
        return desired, progress, maximum_lift_delta

    def _validate_visual_servo_step(
        self,
        previous: dict[str, float],
        desired: dict[str, float],
        joint_names: tuple[str, ...],
    ) -> tuple[bool, str]:
        maximum_step = max(
            0.1,
            float(self.config.get("visual_servo_max_joint_step", 6.0)),
        )
        jumps = {
            name: float(desired[name]) - float(previous[name])
            for name in joint_names
            if abs(float(desired[name]) - float(previous[name])) > maximum_step
        }
        if jumps:
            return False, (
                "Visual-servo command jump rejected before transmission: "
                f"{jumps}; maximum per-command joint step is {maximum_step:.1f}"
            )
        return True, "Visual-servo command step is bounded"

    def _tick_approach(
        self,
        detection: Detection,
        frame_size: tuple[int, int],
        tof_reading: Optional[TofReading],
    ) -> None:
        width, height = frame_size
        _, _, box_width, box_height = detection.bbox
        area_ratio = box_width * box_height / float(width * height)
        bbox_height_ratio = box_height / float(height)
        tof_enabled = bool(self.config.get("tof_enabled", False))
        tof_control_enabled = bool(self.config.get("tof_control_enabled", False))

        if (
            bool(self.config.get("visual_handoff_enabled", True))
            and tof_control_enabled
            and tof_reading is not None
            and tof_reading.valid
            and tof_reading.range_m is not None
        ):
            handoff_height_ratio = float(
                self.config.get("visual_handoff_bbox_height_ratio", 0.85)
            )
            handoff_area_ratio = float(
                self.config.get("visual_handoff_area_ratio", 0.18)
            )
            handoff_tof_m = float(
                self.config.get("visual_handoff_tof_m", 0.19)
            )
            handoff_max_tof_m = max(
                handoff_tof_m,
                float(self.config.get("visual_handoff_max_tof_m", 0.21)),
            )
            visual_reasons = []
            if bbox_height_ratio >= handoff_height_ratio:
                visual_reasons.append(
                    f"bbox_height={bbox_height_ratio:.3f}>={handoff_height_ratio:.3f}"
                )
            if area_ratio >= handoff_area_ratio:
                visual_reasons.append(f"area={area_ratio:.3f}>={handoff_area_ratio:.3f}")
            tof_close = tof_reading.range_m <= handoff_tof_m
            visual_close = (
                bool(visual_reasons)
                and tof_reading.range_m <= handoff_max_tof_m
            )
            if tof_close or visual_close:
                reasons = list(visual_reasons)
                reasons.append(
                    f"tof={tof_reading.range_m:.3f}<={handoff_tof_m:.3f}m"
                    if tof_close
                    else f"tof={tof_reading.range_m:.3f}<={handoff_max_tof_m:.3f}m"
                )
                self.state = ServoState.FINAL_APPROACH
                self._final_approach_started = time.monotonic()
                self._final_approach_start_lift = None
                self._final_approach_command_target = None
                self._last_final_approach_command = 0.0
                self._final_approach_endpoint_reached_at = 0.0
                reason = ", ".join(reasons)
                self.message = (
                    "Visual handoff complete; final ToF-guided horizontal approach "
                    f"started ({reason})"
                )
                self._debug(
                    "visual_handoff_started",
                    bbox=detection.bbox,
                    confidence=detection.confidence,
                    area_ratio=area_ratio,
                    bbox_height_ratio=bbox_height_ratio,
                    tof_range_m=tof_reading.range_m,
                    reasons=reasons,
                )
                self._tick_final_approach(tof_reading)
                return

        if tof_enabled and tof_reading is not None and tof_reading.valid:
            grasp_distance = float(self.config.get("tof_grasp_distance_m", 0.06))
            if tof_reading.range_m is not None and tof_reading.range_m <= grasp_distance:
                if tof_control_enabled:
                    ok, message = self.arm.set_gripper(float(self.config["gripper_close"]))
                    self.state = ServoState.GRASPED if ok else ServoState.ERROR
                    self.message = "Grasp complete at ToF distance" if ok else message
                    return
                self.message = (
                    f"ToF shadow: would grasp at {tof_reading.range_m:.3f} m"
                )
            elif tof_control_enabled:
                self.state = ServoState.APPROACHING
                self.message = f"ToF approach distance {tof_reading.range_m:.3f} m"
        elif tof_control_enabled:
            self.state = ServoState.RANGE_WAIT
            reason = tof_reading.reason if tof_reading is not None else "no_range"
            self.message = f"Waiting for valid ToF range: {reason}"
            return

        if not tof_control_enabled and area_ratio >= float(self.config["grasp_area_threshold"]):
            ok, message = self.arm.set_gripper(float(self.config["gripper_close"]))
            self.state = ServoState.GRASPED if ok else ServoState.ERROR
            self.message = "Grasp complete" if ok else message
            return
        joints = self.arm.get_joints()
        if not joints:
            self.state = ServoState.ERROR
            self.message = "Unable to read joints for approach"
            return
        feedback_lift = float(joints["shoulder_lift.pos"])
        previous_lift = float(
            (self._approach_command_target or {}).get(
                "shoulder_lift.pos", feedback_lift
            )
        )
        maximum_command_lead = max(
            float(self.config["approach_step"]),
            float(self.config.get("approach_max_command_lead", 4.0)),
        )
        requested_lift = min(
            feedback_lift + maximum_command_lead,
            previous_lift + float(self.config["approach_step"]),
        )
        desired, profile_progress, maximum_lift_delta = self._taught_approach_target(
            joints,
            requested_lift,
        )
        previous_target = self._approach_command_target or {
            name: float(joints[name])
            for name in (
                "shoulder_lift.pos",
                "elbow_flex.pos",
                "wrist_flex.pos",
            )
        }
        if (
            profile_progress >= 1.0
            and float(previous_target["shoulder_lift.pos"])
            >= float(desired["shoulder_lift.pos"])
        ):
            self.state = ServoState.ERROR
            self.message = (
                "Taught horizontal approach reached its saved endpoint "
                f"shoulder_lift={desired['shoulder_lift.pos']:.2f} before the ToF "
                "handoff threshold"
            )
            return
        accepted, message = self._validate_servo_joint_targets(
            desired,
            ("shoulder_lift.pos", "elbow_flex.pos", "wrist_flex.pos"),
        )
        if not accepted:
            self.state = ServoState.ERROR
            self.message = (
                f"{message}; adjust the pregrasp pose, camera alignment, or ToF threshold"
            )
            return
        accepted, message = self._validate_visual_servo_step(
            previous_target,
            desired,
            ("shoulder_lift.pos", "elbow_flex.pos", "wrist_flex.pos"),
        )
        if not accepted:
            self.state = ServoState.ERROR
            self.message = message
            return
        ok, message = self.arm.send_arm_joints(desired)
        if not ok:
            self.state = ServoState.ERROR
            self.message = message
            return
        self._approach_command_target = {
            "shoulder_lift.pos": float(desired["shoulder_lift.pos"]),
            "elbow_flex.pos": float(desired["elbow_flex.pos"]),
            "wrist_flex.pos": float(desired["wrist_flex.pos"]),
        }
        self.message = (
            f"Taught horizontal approach {profile_progress * 100.0:.0f}%; "
            + (
                f"ToF {tof_reading.range_m:.3f} m"
                if tof_reading is not None
                and tof_reading.valid
                and tof_reading.range_m is not None
                else "waiting for ToF"
            )
        )
        self._debug(
            "approach_tick",
            bbox=detection.bbox,
            tof_range_m=(
                tof_reading.range_m
                if tof_reading is not None and tof_reading.valid
                else None
            ),
            joints=dict(joints),
            desired=dict(desired),
            profile_start=dict(self._approach_profile_start or {}),
            profile_progress=profile_progress,
            maximum_lift_delta=maximum_lift_delta,
        )

    def _tick_final_approach(
        self,
        tof_reading: Optional[TofReading],
    ) -> None:
        now = time.monotonic()
        if self._final_approach_started <= 0.0:
            self._final_approach_started = now
        timeout = float(self.config.get("final_approach_timeout_sec", 6.0))
        if now - self._final_approach_started > timeout:
            self.state = ServoState.ERROR
            self.message = "Final blind-zone approach timed out before grasp"
            return
        if tof_reading is None or not tof_reading.valid or tof_reading.range_m is None:
            reason = tof_reading.reason if tof_reading is not None else "no_range"
            self.message = (
                f"Final approach holding for valid ToF; YOLO is optional here ({reason})"
            )
            return
        grasp_distance = float(self.config.get("final_grasp_tof_m", 0.090))
        if tof_reading.range_m <= grasp_distance:
            ok, message = self.arm.set_gripper(float(self.config["gripper_close"]))
            self.state = ServoState.GRASPED if ok else ServoState.ERROR
            self.message = (
                f"Final grasp complete at ToF {tof_reading.range_m:.3f} m"
                if ok
                else message
            )
            self._debug(
                "final_grasp_triggered",
                tof_range_m=tof_reading.range_m,
                threshold_m=grasp_distance,
                success=ok,
            )
            return
        command_interval = max(
            0.0,
            float(self.config.get("final_approach_command_interval_sec", 0.10)),
        )
        if now - self._last_final_approach_command < command_interval:
            self.message = (
                f"Final ToF approach {tof_reading.range_m:.3f} m; waiting next bounded step"
            )
            return
        joints = self.arm.get_joints()
        if not joints:
            self.state = ServoState.ERROR
            self.message = "Unable to read joints during final approach"
            return
        feedback_lift = float(joints["shoulder_lift.pos"])
        if self._final_approach_start_lift is None:
            self._final_approach_start_lift = feedback_lift
        step = float(self.config.get("final_approach_step", 1.0))
        maximum_lead = max(
            step,
            float(self.config.get("final_approach_max_command_lead", 4.0)),
        )
        previous_profile_target = (
            self._final_approach_command_target
            or self._approach_command_target
            or {
                name: float(joints[name])
                for name in (
                    "shoulder_lift.pos",
                    "elbow_flex.pos",
                    "wrist_flex.pos",
                )
            }
        )
        previous_lift = float(
            previous_profile_target.get(
                "shoulder_lift.pos", feedback_lift
            )
        )
        requested_lift = min(feedback_lift + maximum_lead, previous_lift + step)
        maximum_travel = float(
            self.config.get("final_approach_max_lift_delta", 20.0)
        )
        bounded_requested_lift = min(
            requested_lift,
            self._final_approach_start_lift + maximum_travel,
        )
        desired, profile_progress, profile_maximum_lift_delta = (
            self._taught_approach_target(joints, bounded_requested_lift)
        )
        blind_limit_reached = bounded_requested_lift < requested_lift - 1e-6
        taught_endpoint_reached = (
            float(desired["shoulder_lift.pos"]) < bounded_requested_lift - 1e-6
        )
        accepted, message = self._validate_servo_joint_targets(
            desired,
            ("shoulder_lift.pos", "elbow_flex.pos", "wrist_flex.pos"),
        )
        if not accepted:
            self.state = ServoState.ERROR
            self.message = f"{message}; final blind-zone approach stopped"
            return
        command_required = any(
            abs(float(desired[name]) - float(previous_profile_target[name])) > 1e-6
            for name in (
                "shoulder_lift.pos",
                "elbow_flex.pos",
                "wrist_flex.pos",
            )
        )
        if command_required:
            accepted, message = self._validate_visual_servo_step(
                previous_profile_target,
                desired,
                ("shoulder_lift.pos", "elbow_flex.pos", "wrist_flex.pos"),
            )
            if not accepted:
                self.state = ServoState.ERROR
                self.message = f"{message}; final blind-zone approach stopped"
                return
            ok, message = self.arm.send_arm_joints(desired)
            if not ok:
                self.state = ServoState.ERROR
                self.message = message
                return
            self._final_approach_command_target = {
                "shoulder_lift.pos": float(desired["shoulder_lift.pos"]),
                "elbow_flex.pos": float(desired["elbow_flex.pos"]),
                "wrist_flex.pos": float(desired["wrist_flex.pos"]),
            }
        self._last_final_approach_command = now
        if blind_limit_reached or taught_endpoint_reached:
            if self._final_approach_endpoint_reached_at <= 0.0:
                self._final_approach_endpoint_reached_at = now
            settle_sec = max(
                0.0,
                float(
                    self.config.get(
                        "final_approach_endpoint_settle_sec",
                        0.75,
                    )
                ),
            )
            elapsed_settle = now - self._final_approach_endpoint_reached_at
            self._debug(
                "final_approach_endpoint_hold",
                tof_range_m=tof_reading.range_m,
                feedback_lift=feedback_lift,
                desired_lift=float(desired["shoulder_lift.pos"]),
                blind_limit_reached=blind_limit_reached,
                taught_endpoint_reached=taught_endpoint_reached,
                elapsed_settle=elapsed_settle,
                settle_sec=settle_sec,
            )
            if elapsed_settle < settle_sec:
                self.message = (
                    f"Final endpoint commanded; holding {settle_sec - elapsed_settle:.2f}s "
                    f"for joint and ToF settling at {tof_reading.range_m:.3f} m"
                )
                return
            self.state = ServoState.ERROR
            self.message = (
                (
                    "Final blind-zone approach reached its maximum taught travel "
                    f"{maximum_travel:.1f} before ToF grasp threshold"
                )
                if blind_limit_reached
                else (
                    "Final approach reached the saved taught endpoint "
                    f"shoulder_lift={desired['shoulder_lift.pos']:.2f} before the ToF "
                    "grasp threshold; record a longer pure grasp demo or use a verified "
                    "larger ToF close threshold"
                )
            )
            return
        self.message = (
            f"Final ToF-guided horizontal approach {tof_reading.range_m:.3f} m; "
            "YOLO loss is expected"
        )
        self._debug(
            "final_approach_tick",
            tof_range_m=tof_reading.range_m,
            joints=dict(joints),
            desired=dict(desired),
            start_lift=self._final_approach_start_lift,
            maximum_travel=maximum_travel,
            profile_progress=profile_progress,
            profile_maximum_lift_delta=profile_maximum_lift_delta,
        )

    def _tick_move(self) -> None:
        if self._move_target is None or self._move_start is None:
            self.state = ServoState.IDLE
            return
        elapsed = time.monotonic() - self._move_started
        timeout = float(self.config["move_timeout_sec"])
        progress = min(1.0, elapsed / max(0.001, self._move_duration))
        smooth_progress = progress * progress * (3.0 - 2.0 * progress)
        desired = {
            name: self._move_start[name]
            + (self._move_target[name] - self._move_start[name]) * smooth_progress
            for name in ARM_JOINTS
        }
        ok, message = self.arm.send_arm_joints(desired)
        if not ok:
            self.state = ServoState.ERROR
            self.message = message
            self._debug(
                "preset_move_command_failed",
                preset=self._move_name,
                elapsed_sec=elapsed,
                desired=desired,
                message=message,
            )
            return
        joints = (
            self.arm.get_joints()
            if progress >= 1.0 or self._debug_callback is not None
            else {}
        )
        errors = (
            {
                name: self._move_target[name] - float(joints[name])
                for name in ARM_JOINTS
                if name in joints
            }
            if joints
            else {}
        )
        self._debug(
            "preset_move_tick",
            preset=self._move_name,
            elapsed_sec=elapsed,
            progress=progress,
            desired=desired,
            feedback=dict(joints),
            errors=errors,
        )
        if progress < 1.0:
            if joints:
                self._move_last_feedback = {
                    name: float(joints[name]) for name in ARM_JOINTS if name in joints
                }
            if elapsed > timeout:
                self._fail_preset_timeout(joints or None)
                return
            self.message = f"Moving smoothly to {self._move_name}: {progress * 100:.0f}%"
            return
        if not joints:
            self._move_settle_cycles = 0
            self._move_near_settle_cycles = 0
            self._move_last_feedback = None
            if elapsed > timeout:
                self._fail_preset_timeout(None)
                return
            self.message = "Waiting for joint feedback at preset target"
            return
        strict_at_target = all(
            abs(errors[name]) <= self._arrival_threshold(name)
            for name in ARM_JOINTS
        )
        stable_margin = float(self.config.get("arrive_stable_margin", 0.75))
        near_target = all(
            abs(errors[name]) <= self._arrival_threshold(name) + stable_margin
            for name in ARM_JOINTS
        )
        feedback_delta = None
        if self._move_last_feedback is not None and all(
            name in self._move_last_feedback for name in ARM_JOINTS
        ):
            feedback_delta = max(
                abs(float(joints[name]) - self._move_last_feedback[name])
                for name in ARM_JOINTS
            )
        stable_delta = float(self.config.get("arrive_stable_delta", 0.35))
        feedback_stable = feedback_delta is not None and feedback_delta <= stable_delta
        self._move_last_feedback = {
            name: float(joints[name]) for name in ARM_JOINTS
        }
        self._move_settle_cycles = (
            self._move_settle_cycles + 1 if strict_at_target else 0
        )
        self._move_near_settle_cycles = (
            self._move_near_settle_cycles + 1
            if near_target and feedback_stable
            else 0
        )
        stable_cycles = int(self.config.get("arrive_stable_cycles", 5))
        if self._move_settle_cycles >= 3 or (strict_at_target and elapsed > timeout):
            self._complete_preset_move("strict", errors)
            return
        if self._move_near_settle_cycles >= stable_cycles:
            self._complete_preset_move("stable_near", errors)
            return
        if elapsed > timeout:
            self._fail_preset_timeout(joints)
            return
        largest_name = max(errors, key=lambda name: abs(errors[name]))
        self.message = (
            f"Settling at preset {self._move_name}; largest feedback error "
            f"{largest_name}={errors[largest_name]:+.2f}, "
            f"tolerance={self._arrival_threshold(largest_name):.2f}, "
            f"stable_delta={feedback_delta if feedback_delta is not None else float('nan'):.2f}"
        )

    def _complete_preset_move(
        self,
        arrival_mode: str,
        errors: dict[str, float],
    ) -> None:
        completion_state = self._move_completion_state
        move_name = self._move_name
        self._debug(
            "preset_move_completed",
            preset=move_name,
            arrival_mode=arrival_mode,
            errors=dict(errors),
            strict_cycles=self._move_settle_cycles,
            stable_near_cycles=self._move_near_settle_cycles,
        )
        self._move_target = None
        self._move_start = None
        self._move_completion_state = ServoState.IDLE
        self._move_name = ""
        self._move_duration = 0.0
        self._move_settle_cycles = 0
        self._move_near_settle_cycles = 0
        self._move_last_feedback = None
        self.state = completion_state
        if completion_state == ServoState.CENTERING:
            self._reset_centering_filter()
            self._grasp_started = time.monotonic()
        suffix = "" if arrival_mode == "strict" else " (stable near target)"
        self.message = (
            f"Pregrasp reached; centering target{suffix}"
            if completion_state == ServoState.CENTERING
            else f"Preset move complete: {move_name}{suffix}"
        )

    def _arrival_threshold(self, joint_name: str) -> float:
        if joint_name == "elbow_flex.pos":
            return float(
                self.config.get(
                    "elbow_arrive_threshold",
                    self.config.get("arrive_threshold", 2.0),
                )
            )
        return float(self.config.get("arrive_threshold", 2.0))

    def _fail_preset_timeout(self, joints: Optional[dict[str, float]]) -> None:
        move_name = self._move_name
        target = dict(self._move_target or {})
        if joints:
            errors = {
                name: target[name] - float(joints[name])
                for name in ARM_JOINTS
                if name in target
                and name in joints
                and abs(target[name] - float(joints[name]))
                > self._arrival_threshold(name)
            }
            detail = ", ".join(
                f"{name} error={error:+.2f} "
                f"tolerance={self._arrival_threshold(name):.2f}"
                for name, error in errors.items()
            )
            if not detail:
                detail = "feedback was received but did not remain stable for arrival confirmation"
        else:
            detail = "joint feedback was unavailable"
            errors = {}
        self._debug(
            "preset_move_timeout",
            preset=move_name,
            target=target,
            feedback=dict(joints or {}),
            errors=errors,
            detail=detail,
            strict_cycles=self._move_settle_cycles,
            stable_near_cycles=self._move_near_settle_cycles,
        )
        self._move_target = None
        self._move_start = None
        self._move_completion_state = ServoState.IDLE
        self._move_name = ""
        self._move_duration = 0.0
        self._move_settle_cycles = 0
        self._move_near_settle_cycles = 0
        self._move_last_feedback = None
        self.state = ServoState.ERROR
        self.message = f"Preset move timed out ({move_name}): {detail}"

    def _validate_servo_joint_targets(
        self,
        desired: dict[str, float],
        joint_names: tuple[str, ...],
    ) -> tuple[bool, str]:
        limit = float(self.config.get("joint_command_limit", 95.0))
        outside = {
            name: float(desired[name])
            for name in joint_names
            if abs(float(desired[name])) > limit
        }
        if outside:
            return False, f"Visual servo joint safety limit +/-{limit:.1f} reached: {outside}"
        return True, "Joint targets are inside the visual-servo safety limits"

    def _record_demo(
        self,
        detection: Optional[Detection],
        frame_size: tuple[int, int],
        tof_reading: Optional[TofReading],
    ) -> None:
        joints = self.arm.get_joints()
        if not joints:
            return
        width, height = frame_size
        target_center_x, target_center_y = self.visual_target_center(frame_size)
        bbox_x = bbox_y = bbox_width = bbox_height = None
        bbox_center_x = bbox_center_y = bbox_area_px = bbox_area_ratio = None
        error_x = error_y = None
        if detection is not None:
            bbox_x, bbox_y, bbox_width, bbox_height = detection.bbox
            bbox_center_x = bbox_x + bbox_width / 2.0
            bbox_center_y = bbox_y + bbox_height / 2.0
            bbox_area_px = bbox_width * bbox_height
            bbox_area_ratio = bbox_area_px / float(width * height)
            error_x = (bbox_center_x - target_center_x) / width
            error_y = (bbox_center_y - target_center_y) / height
        row = {
            "sample_index": len(self.demo_rows),
            "time_unix": time.time(),
            "elapsed_sec": max(
                0.0,
                time.monotonic() - self._demo_started_monotonic,
            ),
            "target": self._demo_target,
            "state": self.state.value,
            "frame_width": width,
            "frame_height": height,
            "target_center_x": target_center_x,
            "target_center_y": target_center_y,
            "detection_present": detection is not None,
            "detection_trusted": detection.trusted if detection is not None else False,
            "detection_sequence": detection.sequence if detection is not None else 0,
            "confidence": detection.confidence if detection is not None else 0.0,
            "bbox_x": bbox_x,
            "bbox_y": bbox_y,
            "bbox_width": bbox_width,
            "bbox_height": bbox_height,
            "bbox_center_x": bbox_center_x,
            "bbox_center_y": bbox_center_y,
            "bbox_area_px": bbox_area_px,
            "bbox_area_ratio": bbox_area_ratio,
            "error_x": error_x,
            "error_y": error_y,
            "tof_range_m": (
                tof_reading.range_m if tof_reading is not None else None
            ),
            "tof_age_sec": (
                tof_reading.age_sec if tof_reading is not None else None
            ),
            "tof_valid": tof_reading.valid if tof_reading is not None else False,
            "tof_reason": tof_reading.reason if tof_reading is not None else "",
            "shoulder_pan_pos": joints.get("shoulder_pan.pos"),
            "shoulder_lift_pos": joints.get("shoulder_lift.pos"),
            "elbow_flex_pos": joints.get("elbow_flex.pos"),
            "wrist_flex_pos": joints.get("wrist_flex.pos"),
            "wrist_roll_pos": joints.get("wrist_roll.pos"),
            "gripper_pos": joints.get("gripper.pos"),
        }
        self.demo_rows.append(row)
