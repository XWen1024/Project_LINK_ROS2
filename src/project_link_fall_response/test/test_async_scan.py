from project_link_fall_response.async_scan import AsyncScanOrchestrator
from project_link_fall_response.fall_models import FallBatchResult, PersonBatchResult


def fall_result(angle, scores):
    return FallBatchResult(
        angle_deg=angle,
        fallen_scores=tuple(scores),
        best_confidence=max(scores, default=0.0),
        best_frame_index=max(range(len(scores)), key=scores.__getitem__) if scores else 0,
        labels_seen=("fallen",) if max(scores, default=0.0) else ("standing",),
    )


def make_scan():
    return AsyncScanOrchestrator(
        angles=tuple(range(0, 360, 30)),
        simulated_angle_delay_sec=0.03,
    )


def test_strong_candidate_interrupts_and_rechecks_before_full_scan():
    captures = []

    def capture(angle, count, stage, _step, _total):
        captures.append((angle, count, stage))
        return [f"{angle}-{stage}-{index}".encode() for index in range(count)]

    def infer(angle, frames):
        scores = [0.82] * len(frames) if angle == 0 else [0.0] * len(frames)
        return fall_result(angle, scores)

    outcome = make_scan().run(
        capture=capture,
        infer_fall=infer,
        infer_people=lambda _frames: PersonBatchResult((), 0.0, 0),
        cancelled=lambda: False,
        feedback=lambda *_args: None,
    )

    assert outcome.kind == "confirmed_candidate"
    assert outcome.angle_deg == 0
    assert outcome.angles_completed == 1
    assert len(outcome.vlm_images) == 3
    assert captures[-1] == (0, 2, "candidate_recheck")


def test_failed_recheck_resumes_and_world_fallback_covers_all_angles():
    rechecked = False

    def capture(angle, count, stage, _step, _total):
        nonlocal rechecked
        if stage == "candidate_recheck":
            rechecked = True
        return [f"{angle}-{stage}-{index}".encode() for index in range(count)]

    def infer(angle, frames):
        if angle == 0 and not rechecked:
            return fall_result(angle, [0.80] * len(frames))
        return fall_result(angle, [0.05] * len(frames))

    seen = {}

    def people(frames):
        seen["angles"] = [angle for angle, _jpeg in frames]
        return PersonBatchResult((90.0,), 0.75, 3)

    outcome = make_scan().run(
        capture=capture,
        infer_fall=infer,
        infer_people=people,
        cancelled=lambda: False,
        feedback=lambda *_args: None,
    )

    assert outcome.kind == "world_fallback"
    assert seen["angles"] == list(range(0, 360, 30))
    assert len(outcome.vlm_images) == 12


def test_weak_candidate_waits_for_full_scan_then_requests_review():
    def capture(angle, count, stage, _step, _total):
        return [f"{angle}-{stage}-{index}".encode() for index in range(count)]

    def infer(angle, frames):
        score = 0.40 if angle == 120 else 0.0
        return fall_result(angle, [score] * len(frames))

    outcome = make_scan().run(
        capture=capture,
        infer_fall=infer,
        infer_people=lambda _frames: PersonBatchResult((), 0.0, 0),
        cancelled=lambda: False,
        feedback=lambda *_args: None,
    )

    assert outcome.kind == "weak_candidate"
    assert outcome.angle_deg == 120
    assert outcome.angles_completed == 12


def test_world_without_people_is_degraded_not_not_fall():
    outcome = make_scan().run(
        capture=lambda angle, count, stage, _step, _total: [
            f"{angle}-{stage}-{index}".encode() for index in range(count)
        ],
        infer_fall=lambda angle, frames: fall_result(angle, [0.0] * len(frames)),
        infer_people=lambda _frames: PersonBatchResult((), 0.0, 0),
        cancelled=lambda: False,
        feedback=lambda *_args: None,
    )
    assert outcome.kind == "degraded"
    assert "no person" in outcome.reason


def test_specialized_inference_failure_falls_back_to_world():
    outcome = make_scan().run(
        capture=lambda angle, count, stage, _step, _total: [
            f"{angle}-{stage}-{index}".encode() for index in range(count)
        ],
        infer_fall=lambda _angle, _frames: (_ for _ in ()).throw(RuntimeError("model failed")),
        infer_people=lambda _frames: PersonBatchResult((30.0,), 0.8, 1),
        cancelled=lambda: False,
        feedback=lambda *_args: None,
    )
    assert outcome.kind == "world_fallback"
    assert len(outcome.vlm_images) == 12


def test_real_heading_mover_can_interrupt_and_resume_a_spin_segment():
    import time

    moves = []
    recheck_failed = False

    def capture(angle, count, stage, _step, _total):
        nonlocal recheck_failed
        if stage == "candidate_recheck":
            recheck_failed = True
        return [f"{angle}-{stage}-{index}".encode() for index in range(count)]

    def infer(angle, frames):
        if angle == 0 and not recheck_failed:
            return fall_result(angle, [0.80] * len(frames))
        return fall_result(angle, [0.0] * len(frames))

    def move(target, stage, _step, _total, should_interrupt):
        moves.append((target, stage))
        deadline = time.monotonic() + 0.05
        while time.monotonic() < deadline:
            if should_interrupt():
                return False
            time.sleep(0.001)
        return True

    outcome = make_scan().run(
        capture=capture,
        infer_fall=infer,
        infer_people=lambda _frames: PersonBatchResult((), 0.0, 0),
        cancelled=lambda: False,
        feedback=lambda *_args: None,
        move_to_heading=move,
    )

    assert outcome.kind == "degraded"
    assert moves.count((30, "scan_move")) == 2
    assert (0, "candidate_return") in moves
    assert moves[-1] == (360.0, "return_to_start")
