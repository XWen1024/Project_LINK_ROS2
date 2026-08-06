import unittest

from project_link_uwb_navigation.policy import (
    GoalThrottler,
    PersonMode,
    PolicyConfig,
    propose_goal,
    should_recompute_person_target,
    should_submit_nav_goal,
    target_speed_mps,
)


class PolicyTests(unittest.TestCase):
    def test_follow_goal_stays_behind_person(self) -> None:
        decision = propose_goal(PersonMode.FOLLOW, 0.0, 0.0, 3.0, 0.0, PolicyConfig())
        self.assertEqual(decision.action, "navigate")
        self.assertAlmostEqual(decision.goal_x_m, 1.5)
        self.assertAlmostEqual(decision.goal_y_m, 0.0)

    def test_follow_holds_and_never_generates_reverse_goal(self) -> None:
        inside = propose_goal(PersonMode.FOLLOW, 0.0, 0.0, 1.5, 0.0, PolicyConfig())
        too_close = propose_goal(PersonMode.FOLLOW, 0.0, 0.0, 0.8, 0.0, PolicyConfig())
        self.assertEqual(inside.action, "hold")
        self.assertEqual(too_close.action, "hold")
        self.assertIsNone(too_close.goal_x_m)
        self.assertEqual(too_close.reason, "person_too_close_no_reverse")

    def test_summon_completes_inside_arrival_distance(self) -> None:
        decision = propose_goal(PersonMode.SUMMON, 0.0, 0.0, 1.1, 0.0, PolicyConfig())
        self.assertEqual(decision.action, "arrived")

    def test_goal_throttler_uses_displacement_or_refresh(self) -> None:
        throttler = GoalThrottler(displacement_m=0.2, refresh_sec=0.75)
        self.assertTrue(throttler.should_replace(1.0, 2.0, 1_000_000_000))
        throttler.mark_submitted(1.0, 2.0, 1_000_000_000)
        self.assertFalse(throttler.should_replace(1.1, 2.0, 1_500_000_000))
        self.assertTrue(throttler.should_replace(1.3, 2.0, 1_500_000_000))
        self.assertTrue(throttler.should_replace(1.1, 2.0, 1_800_000_000))

    def test_summon_submits_one_nav_goal_but_follow_keeps_rolling(self) -> None:
        throttler = GoalThrottler(displacement_m=0.2, refresh_sec=0.75)
        self.assertTrue(
            should_submit_nav_goal(PersonMode.SUMMON, False, throttler, 1.0, 2.0, 1_000_000_000)
        )
        self.assertFalse(
            should_submit_nav_goal(PersonMode.SUMMON, True, throttler, 1.3, 2.0, 2_000_000_000)
        )
        self.assertTrue(
            should_submit_nav_goal(PersonMode.FOLLOW, False, throttler, 1.0, 2.0, 1_000_000_000)
        )

    def test_summon_freezes_target_after_submission(self) -> None:
        self.assertTrue(should_recompute_person_target(PersonMode.SUMMON, False))
        self.assertFalse(should_recompute_person_target(PersonMode.SUMMON, True))
        self.assertTrue(should_recompute_person_target(PersonMode.FOLLOW, True))

    def test_target_speed_uses_monotonic_observation_time(self) -> None:
        self.assertAlmostEqual(target_speed_mps(0.0, 0.0, 1_000_000_000, 0.5, 0.0, 1_500_000_000), 1.0)
        with self.assertRaisesRegex(ValueError, "target_time_not_increasing"):
            target_speed_mps(0.0, 0.0, 2, 0.0, 0.0, 1)
