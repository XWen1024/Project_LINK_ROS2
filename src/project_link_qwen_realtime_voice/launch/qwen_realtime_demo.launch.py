"""Launch Qwen realtime voice in bounded no-SLAM demo-motion mode."""

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource


def generate_launch_description() -> LaunchDescription:
    source = PythonLaunchDescriptionSource(
        get_package_share_directory("project_link_qwen_realtime_voice")
        + "/launch/qwen_realtime_voice.launch.py"
    )
    return LaunchDescription([
        IncludeLaunchDescription(
            source,
            launch_arguments={
                "navigation_backend": "direct_drive",
                "enable_motion": "false",
                "enable_visual_grasp": "false",
                "enable_demo_motion": "true",
                "pure_test_mode": "on",
            }.items(),
        )
    ])
