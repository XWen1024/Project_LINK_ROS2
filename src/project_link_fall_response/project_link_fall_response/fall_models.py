"""Local fall-state and person-presence detectors for the mobile workflow."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Any


@dataclass(frozen=True)
class FallBatchResult:
    angle_deg: float
    fallen_scores: tuple[float, ...]
    best_confidence: float
    best_frame_index: int
    labels_seen: tuple[str, ...]

    @property
    def has_fallen(self) -> bool:
        return self.best_confidence > 0.0


@dataclass(frozen=True)
class PersonBatchResult:
    angles_with_people: tuple[float, ...]
    best_confidence: float
    best_frame_index: int

    @property
    def has_person(self) -> bool:
        return self.best_confidence > 0.0


def _decode(jpeg_data: bytes):
    import cv2
    import numpy as np

    frame = cv2.imdecode(np.frombuffer(jpeg_data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("captured JPEG could not be decoded")
    return frame


class SpecializedFallDetector:
    """YOLO detector whose labels are fallen, sitting and standing."""

    def __init__(self, model_path: str, *, threshold: float = 0.05, device: str = "") -> None:
        self.model_path = Path(model_path).expanduser()
        self.threshold = float(threshold)
        self.device = device
        self._model: Any = None
        self._lock = threading.Lock()

    @property
    def ready(self) -> bool:
        return self.model_path.is_file()

    def _load(self):
        if not self.ready:
            raise FileNotFoundError(f"specialized fall model is missing: {self.model_path}")
        if self._model is None:
            from ultralytics import YOLO

            self._model = YOLO(str(self.model_path), task="detect")
        return self._model

    def warmup(self) -> None:
        import numpy as np

        with self._lock:
            model = self._load()
            kwargs: dict[str, Any] = {
                "source": np.zeros((640, 640, 3), dtype=np.uint8),
                "conf": self.threshold,
                "imgsz": 640,
                "verbose": False,
            }
            if self.device:
                kwargs["device"] = self.device
            model.predict(**kwargs)

    def assess(self, angle_deg: float, jpeg_frames: list[bytes]) -> FallBatchResult:
        with self._lock:
            model = self._load()
            fallen_scores: list[float] = []
            labels_seen: set[str] = set()
            best_confidence = 0.0
            best_frame_index = 0
            for frame_index, jpeg_data in enumerate(jpeg_frames):
                kwargs: dict[str, Any] = {
                    "source": _decode(jpeg_data),
                    "conf": self.threshold,
                    "imgsz": 640,
                    "verbose": False,
                }
                if self.device:
                    kwargs["device"] = self.device
                result = model.predict(**kwargs)[0]
                frame_fallen = 0.0
                if result.boxes is not None:
                    for score, class_id in zip(
                        result.boxes.conf.cpu().tolist(), result.boxes.cls.cpu().tolist()
                    ):
                        label = str(result.names[int(class_id)]).lower()
                        labels_seen.add(label)
                        if label == "fallen":
                            frame_fallen = max(frame_fallen, float(score))
                fallen_scores.append(frame_fallen)
                if frame_fallen > best_confidence:
                    best_confidence = frame_fallen
                    best_frame_index = frame_index
        return FallBatchResult(
            angle_deg=float(angle_deg),
            fallen_scores=tuple(fallen_scores),
            best_confidence=best_confidence,
            best_frame_index=best_frame_index,
            labels_seen=tuple(sorted(labels_seen)),
        )


class YoloWorldPersonDetector:
    """YOLO-World fallback that only establishes whether people are visible."""

    def __init__(self, model_path: str, *, threshold: float = 0.50, device: str = "") -> None:
        self.model_path = Path(model_path).expanduser()
        self.threshold = float(threshold)
        self.device = device
        self._model: Any = None

    @property
    def ready(self) -> bool:
        return self.model_path.is_file()

    def _load(self):
        if not self.ready:
            raise FileNotFoundError(f"YOLO-World model is missing: {self.model_path}")
        if self._model is None:
            from ultralytics import YOLO

            self._model = YOLO(str(self.model_path), task="detect")
        return self._model

    def assess(self, angle_frames: list[tuple[float, bytes]]) -> PersonBatchResult:
        model = self._load()
        angles: set[float] = set()
        best_confidence = 0.0
        best_frame_index = 0
        for frame_index, (angle_deg, jpeg_data) in enumerate(angle_frames):
            kwargs: dict[str, Any] = {
                "source": _decode(jpeg_data),
                "conf": self.threshold,
                "imgsz": 1280,
                "verbose": False,
            }
            if self.device:
                kwargs["device"] = self.device
            result = model.predict(**kwargs)[0]
            if result.boxes is None:
                continue
            for score, class_id in zip(
                result.boxes.conf.cpu().tolist(), result.boxes.cls.cpu().tolist()
            ):
                if str(result.names[int(class_id)]).lower() != "person":
                    continue
                angles.add(float(angle_deg))
                if float(score) > best_confidence:
                    best_confidence = float(score)
                    best_frame_index = frame_index
        return PersonBatchResult(tuple(sorted(angles)), best_confidence, best_frame_index)
