"""Launch Qwen realtime voice in bounded no-SLAM demo-motion mode."""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration


def generate_launch_description() -> LaunchDescription:
    default_params = os.path.join(
        get_package_share_directory("project_link_qwen_realtime_voice"),
        "config",
        "qwen_realtime_voice.yaml",
    )
    source = PythonLaunchDescriptionSource(
        get_package_share_directory("project_link_qwen_realtime_voice")
        + "/launch/qwen_realtime_voice.launch.py"
    )
    return LaunchDescription([
        DeclareLaunchArgument("params_file", default_value=default_params),
        IncludeLaunchDescription(
            source,
            launch_arguments={
                "navigation_backend": "direct_drive",
                "params_file": LaunchConfiguration("params_file"),
                "enable_motion": "false",
                "enable_visual_grasp": "false",
                "enable_demo_motion": "true",
                "pure_test_mode": "on",
            }.items(),
        )
    ])
