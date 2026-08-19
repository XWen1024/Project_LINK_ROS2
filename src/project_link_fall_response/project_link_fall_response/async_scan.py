"""Nav2-independent asynchronous scan decision engine."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
import time
from typing import Callable

from .fall_models import FallBatchResult, PersonBatchResult


@dataclass(frozen=True)
class AngleScene:
    angle_deg: float
    frames: tuple[bytes, ...]

    @property
    def overview(self) -> bytes:
        return self.frames[0]


@dataclass(frozen=True)
class ScanOutcome:
    kind: str
    confidence: float
    angle_deg: float | None
    vlm_images: tuple[tuple[str, bytes], ...]
    notification_image: bytes
    reason: str
    angles_completed: int


class AsyncScanOrchestrator:
    """Capture virtual headings while one GPU worker evaluates earlier bursts."""

    def __init__(
        self,
        *,
        angles: tuple[float, ...],
        frames_per_angle: int = 3,
        recheck_frames: int = 2,
        strong_threshold: float = 0.60,
        weak_threshold: float = 0.25,
        recheck_frame_threshold: float = 0.55,
        recheck_average_threshold: float = 0.50,
        simulated_angle_delay_sec: float = 1.0,
    ) -> None:
        self.angles = angles
        self.frames_per_angle = frames_per_angle
        self.recheck_frames = recheck_frames
        self.strong_threshold = strong_threshold
        self.weak_threshold = weak_threshold
        self.recheck_frame_threshold = recheck_frame_threshold
        self.recheck_average_threshold = recheck_average_threshold
        self.simulated_angle_delay_sec = simulated_angle_delay_sec

    def _confirmed(self, result: FallBatchResult) -> bool:
        scores = result.fallen_scores
        return bool(scores) and max(scores) >= self.recheck_frame_threshold and (
            sum(scores) / len(scores)
        ) >= self.recheck_average_threshold

    def run(
        self,
        *,
        capture: Callable[[float, int, str, int, int], list[bytes]],
        infer_fall: Callable[[float, list[bytes]], FallBatchResult],
        infer_people: Callable[[list[tuple[float, bytes]]], PersonBatchResult],
        cancelled: Callable[[], bool],
        feedback: Callable[[str, str, int, int, float], None],
    ) -> ScanOutcome:
        scenes: list[AngleScene] = []
        pending: list[tuple[Future, AngleScene]] = []
        completed: list[tuple[FallBatchResult, AngleScene]] = []
        rejected_angles: set[float] = set()
        inference_errors = 0

        def process_finished() -> tuple[FallBatchResult, AngleScene] | None:
            nonlocal inference_errors
            strong = None
            remaining = []
            for future, scene in pending:
                if not future.done():
                    remaining.append((future, scene))
                    continue
                try:
                    result = future.result()
                except Exception:
                    inference_errors += 1
                    result = FallBatchResult(scene.angle_deg, (), 0.0, 0, ())
                completed.append((result, scene))
                if (
                    strong is None
                    and result.best_confidence >= self.strong_threshold
                    and result.angle_deg not in rejected_angles
                ):
                    strong = (result, scene)
            pending[:] = remaining
            return strong

        def recheck(candidate: tuple[FallBatchResult, AngleScene], step: int):
            result, scene = candidate
            feedback(
                "candidate_recheck",
                f"high-confidence fallen candidate at {result.angle_deg:.0f} degrees; simulated return and recheck",
                step,
                len(self.angles),
                result.best_confidence,
            )
            frames = capture(
                result.angle_deg,
                self.recheck_frames,
                "candidate_recheck",
                step,
                len(self.angles),
            )
            try:
                checked = infer_fall(result.angle_deg, frames)
            except Exception:
                checked = FallBatchResult(result.angle_deg, (), 0.0, 0, ())
            images = ((f"initial angle {result.angle_deg:.0f}", scene.frames[result.best_frame_index]),) + tuple(
                (f"recheck angle {result.angle_deg:.0f} frame {index + 1}", frame)
                for index, frame in enumerate(frames)
            )
            return checked, images

        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fall-inference")
        try:
            for step, angle_deg in enumerate(self.angles, start=1):
                if cancelled():
                    return ScanOutcome("cancelled", 0.0, None, (), b"", "cancelled", step - 1)
                feedback(
                    "scanning_fall_model",
                    f"simulated scan angle {step}/{len(self.angles)} ({angle_deg:.0f} degrees)",
                    step,
                    len(self.angles),
                    0.0,
                )
                frames = capture(
                    angle_deg,
                    self.frames_per_angle,
                    "scanning_fall_model",
                    step,
                    len(self.angles),
                )
                if cancelled() or not frames:
                    return ScanOutcome("cancelled", 0.0, None, (), b"", "cancelled", step - 1)
                scene = AngleScene(angle_deg, tuple(frames))
                scenes.append(scene)
                pending.append((executor.submit(infer_fall, angle_deg, frames), scene))
                deadline = time.monotonic() + self.simulated_angle_delay_sec
                strong = None
                while time.monotonic() < deadline and not cancelled():
                    strong = process_finished()
                    if strong:
                        break
                    time.sleep(0.01)
                if strong:
                    checked, images = recheck(strong, step)
                    if self._confirmed(checked):
                        confidence = max(strong[0].best_confidence, checked.best_confidence)
                        return ScanOutcome(
                            "confirmed_candidate",
                            confidence,
                            strong[0].angle_deg,
                            images,
                            images[0][1],
                            "specialized fall model candidate reproduced during recheck",
                            step,
                        )
                    rejected_angles.add(strong[0].angle_deg)

            while pending and not cancelled():
                strong = process_finished()
                if strong:
                    checked, images = recheck(strong, len(self.angles))
                    if self._confirmed(checked):
                        confidence = max(strong[0].best_confidence, checked.best_confidence)
                        return ScanOutcome(
                            "confirmed_candidate",
                            confidence,
                            strong[0].angle_deg,
                            images,
                            images[0][1],
                            "specialized fall model candidate reproduced after scan",
                            len(self.angles),
                        )
                    rejected_angles.add(strong[0].angle_deg)
                if pending:
                    time.sleep(0.01)
        finally:
            for future, _scene in pending:
                future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)

        if cancelled():
            return ScanOutcome("cancelled", 0.0, None, (), b"", "cancelled", len(scenes))
        eligible = [pair for pair in completed if pair[0].angle_deg not in rejected_angles]
        best = max(eligible, key=lambda pair: pair[0].best_confidence, default=None)
        if best and best[0].best_confidence >= self.weak_threshold:
            checked, images = recheck(best, len(self.angles))
            confidence = max(best[0].best_confidence, checked.best_confidence)
            return ScanOutcome(
                "weak_candidate",
                confidence,
                best[0].angle_deg,
                images,
                images[0][1],
                "best full-scan fallen candidate requires VLM review",
                len(self.angles),
            )

        feedback(
            "world_fallback",
            (
                "specialized model found no credible fallen candidate; checking every angle for people"
                if not inference_errors
                else f"specialized model failed on {inference_errors} angle batches; checking every angle for people"
            ),
            len(self.angles),
            len(self.angles),
            0.0,
        )
        overview = [(scene.angle_deg, scene.overview) for scene in scenes]
        people = infer_people(overview)
        if not people.has_person:
            return ScanOutcome(
                "degraded",
                0.0,
                None,
                (),
                scenes[0].overview if scenes else b"",
                "YOLO-World found no person in the simulated 360-degree coverage",
                len(scenes),
            )
        images = tuple((f"overview angle {angle:.0f}", jpeg) for angle, jpeg in overview)
        return ScanOutcome(
            "world_fallback",
            people.best_confidence,
            None,
            images,
            overview[people.best_frame_index][1],
            f"YOLO-World found people at {len(people.angles_with_people)} simulated angles",
            len(scenes),
        )
