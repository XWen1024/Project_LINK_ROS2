from pathlib import Path
import unittest


class LaunchContractTests(unittest.TestCase):
    def test_person_navigation_source_id_is_bounded_for_fastrtps(self) -> None:
        action_source = (
            Path(__file__).parents[2]
            / "project_link_uwb_interfaces"
            / "action"
            / "PersonNavigation.action"
        ).read_text(encoding="utf-8")

        self.assertIn("string<=32 source_id", action_source)

    def test_private_tag_launch_argument_is_forced_to_string(self) -> None:
        launch_source = (
            Path(__file__).parents[1] / "launch" / "uwb_navigation.launch.py"
        ).read_text(encoding="utf-8")

        self.assertIn(
            '"tag_address": ParameterValue(tag_address, value_type=str)',
            launch_source,
        )
