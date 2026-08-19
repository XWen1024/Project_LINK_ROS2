"""Local YOLO pose candidate scoring for the static no-motion MVP."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PoseCandidate:
    score: float
    box: tuple[float, float, float, float]
    detection_confidence: float
    trustworthy: bool


@dataclass(frozen=True)
class PoseSequenceResult:
    outcome: str
    confidence: float
    reason: str
    best_frame_index: int = 0


def box_iou(left: tuple[float, float, float, float], right: tuple[float, float, float, float]) -> float:
    x1 = max(left[0], right[0])
    y1 = max(left[1], right[1])
    x2 = min(left[2], right[2])
    y2 = min(left[3], right[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union > 0 else 0.0


def score_pose(
    box: tuple[float, float, float, float],
    keypoints: list[tuple[float, float, float]],
    keypoint_threshold: float,
) -> tuple[float, bool]:
    width = max(1.0, box[2] - box[0])
    height = max(1.0, box[3] - box[1])
    aspect_score = min(1.0, max(0.0, (width / height - 0.55) / 0.95))
    core_indices = (5, 6, 11, 12)
    visible = [index for index in core_indices if index < len(keypoints) and keypoints[index][2] >= keypoint_threshold]
    completeness = len(visible) / len(core_indices)
    horizontal_torso = 0.0
    if len(visible) == len(core_indices):
        shoulder_x = (keypoints[5][0] + keypoints[6][0]) / 2.0
        shoulder_y = (keypoints[5][1] + keypoints[6][1]) / 2.0
        hip_x = (keypoints[11][0] + keypoints[12][0]) / 2.0
        hip_y = (keypoints[11][1] + keypoints[12][1]) / 2.0
        dx = abs(hip_x - shoulder_x)
        dy = abs(hip_y - shoulder_y)
        horizontal_torso = dx / max(math.hypot(dx, dy), 1e-6)
    score = 0.45 * aspect_score + 0.40 * horizontal_torso + 0.15 * completeness
    return min(1.0, max(0.0, score)), completeness >= 0.75


def aggregate_candidates(
    frames: list[list[PoseCandidate]],
    candidate_threshold: float,
    stable_frames: int,
    iou_threshold: float = 0.35,
) -> PoseSequenceResult:
    all_candidates = [(index, candidate) for index, candidates in enumerate(frames) for candidate in candidates]
    trustworthy_people = any(candidate.trustworthy for _, candidate in all_candidates)
    if not all_candidates:
        return PoseSequenceResult("degraded", 0.0, "local pose model found no person")
    best_index, anchor = max(all_candidates, key=lambda pair: pair[1].score)
    matches = [
        (index, candidate)
        for index, candidate in all_candidates
        if candidate.score >= candidate_threshold and box_iou(anchor.box, candidate.box) >= iou_threshold
    ]
    matched_frames = {index for index, _ in matches}
    if len(matched_frames) >= stable_frames:
        confidence = sum(candidate.score for _, candidate in matches) / len(matches)
        best_index, _ = max(matches, key=lambda pair: pair[1].score)
        return PoseSequenceResult(
            "candidate",
            confidence,
            f"fall-like pose stable in {len(matched_frames)} frames",
            best_index,
        )
    if trustworthy_people:
        return PoseSequenceResult("not_fall", anchor.score, "people visible without a stable fall-like pose", best_index)
    return PoseSequenceResult("degraded", anchor.score, "person keypoints were insufficient for a trusted decision", best_index)


class YoloPoseDetector:
    def __init__(
        self,
        model_path: str,
        detection_threshold: float = 0.45,
        keypoint_threshold: float = 0.30,
        candidate_threshold: float = 0.65,
        stable_frames: int = 3,
        device: str = "",
    ) -> None:
        self.model_path = Path(model_path).expanduser()
        self.detection_threshold = float(detection_threshold)
        self.keypoint_threshold = float(keypoint_threshold)
        self.candidate_threshold = float(candidate_threshold)
        self.stable_frames = int(stable_frames)
        self.device = device
        self._model: Any = None

    @property
    def ready(self) -> bool:
        return self.model_path.is_file()

    def _load(self) -> Any:
        if not self.ready:
            raise FileNotFoundError(f"YOLO pose model is missing: {self.model_path}")
        if self._model is None:
            from ultralytics import YOLO

            self._model = YOLO(str(self.model_path))
        return self._model

    def assess(self, jpeg_frames: list[bytes]) -> PoseSequenceResult:
        import cv2
        import numpy as np

        model = self._load()
        frame_candidates: list[list[PoseCandidate]] = []
        for jpeg_data in jpeg_frames:
            frame = cv2.imdecode(np.frombuffer(jpeg_data, dtype=np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                frame_candidates.append([])
                continue
            kwargs: dict[str, Any] = {"source": frame, "conf": self.detection_threshold, "verbose": False}
            if self.device:
                kwargs["device"] = self.device
            result = model.predict(**kwargs)[0]
            candidates: list[PoseCandidate] = []
            boxes = result.boxes.xyxy.cpu().tolist() if result.boxes is not None else []
            confidences = result.boxes.conf.cpu().tolist() if result.boxes is not None else []
            keypoints = result.keypoints.data.cpu().tolist() if result.keypoints is not None else []
            for index, box_values in enumerate(boxes):
                points = [tuple(map(float, point[:3])) for point in keypoints[index]] if index < len(keypoints) else []
                box = tuple(map(float, box_values[:4]))
                score, trustworthy = score_pose(box, points, self.keypoint_threshold)
                detection = float(confidences[index]) if index < len(confidences) else 0.0
                candidates.append(PoseCandidate(score, box, detection, trustworthy))
            frame_candidates.append(candidates)
        return aggregate_candidates(
            frame_candidates,
            candidate_threshold=self.candidate_threshold,
            stable_frames=self.stable_frames,
        )
