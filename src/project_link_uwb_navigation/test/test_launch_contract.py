from pathlib import Path
import unittest


class LaunchContractTests(unittest.TestCase):
    def test_uwb_entrypoints_default_to_cyclone_dds(self) -> None:
        repository_root = Path(__file__).parents[3]
        for script_name in ("navigation_two_start_uwb.sh", "navigation_two_uwb.sh"):
            script_source = (repository_root / script_name).read_text(encoding="utf-8")
            self.assertIn(
                'PROJECT_LINK_UWB_RMW_IMPLEMENTATION:-rmw_cyclonedds_cpp',
                script_source,
            )

    def test_person_navigation_goal_has_no_dynamic_string(self) -> None:
        action_source = (
            Path(__file__).parents[2]
            / "project_link_uwb_interfaces"
            / "action"
            / "PersonNavigation.action"
        ).read_text(encoding="utf-8")

        goal_source = action_source.split("---", maxsplit=1)[0]
        self.assertNotIn("string", goal_source)
        self.assertIn("uint8 mode", goal_source)

    def test_private_tag_launch_argument_is_forced_to_string(self) -> None:
        launch_source = (
            Path(__file__).parents[1] / "launch" / "uwb_navigation.launch.py"
        ).read_text(encoding="utf-8")

        self.assertIn(
            '"tag_address": ParameterValue(tag_address, value_type=str)',
            launch_source,
        )
