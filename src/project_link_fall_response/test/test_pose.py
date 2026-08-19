from project_link_fall_response.pose import PoseCandidate, aggregate_candidates, box_iou, score_pose


def candidate(score, box=(0.0, 0.0, 100.0, 50.0), trustworthy=True):
    return PoseCandidate(score, box, 0.9, trustworthy)


def test_iou_matches_identical_boxes():
    assert box_iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0


def test_three_stable_frames_produce_candidate():
    result = aggregate_candidates(
        [[candidate(0.8)], [candidate(0.75)], [candidate(0.85)], [], []],
        candidate_threshold=0.65,
        stable_frames=3,
    )
    assert result.outcome == "candidate"
    assert result.confidence > 0.7


def test_visible_non_fall_person_is_not_degraded():
    result = aggregate_candidates(
        [[candidate(0.3)], [candidate(0.35)]],
        candidate_threshold=0.65,
        stable_frames=3,
    )
    assert result.outcome == "not_fall"


def test_missing_people_is_degraded():
    assert aggregate_candidates([[], []], 0.65, 3).outcome == "degraded"


def test_horizontal_torso_scores_higher_than_vertical():
    base = [(0.0, 0.0, 0.0)] * 17
    horizontal = list(base)
    horizontal[5] = (20.0, 20.0, 0.9)
    horizontal[6] = (20.0, 30.0, 0.9)
    horizontal[11] = (80.0, 20.0, 0.9)
    horizontal[12] = (80.0, 30.0, 0.9)
    vertical = list(base)
    vertical[5] = (50.0, 10.0, 0.9)
    vertical[6] = (60.0, 10.0, 0.9)
    vertical[11] = (50.0, 90.0, 0.9)
    vertical[12] = (60.0, 90.0, 0.9)
    horizontal_score, _ = score_pose((0, 0, 100, 50), horizontal, 0.3)
    vertical_score, _ = score_pose((0, 0, 50, 100), vertical, 0.3)
    assert horizontal_score > vertical_score
