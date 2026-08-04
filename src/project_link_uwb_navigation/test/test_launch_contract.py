from pathlib import Path
import unittest


class LaunchContractTests(unittest.TestCase):
    def test_private_tag_launch_argument_is_forced_to_string(self) -> None:
        launch_source = (
            Path(__file__).parents[1] / "launch" / "uwb_navigation.launch.py"
        ).read_text(encoding="utf-8")

        self.assertIn(
            '"tag_address": ParameterValue(tag_address, value_type=str)',
            launch_source,
        )
