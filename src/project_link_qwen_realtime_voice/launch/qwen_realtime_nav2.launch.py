"""Launch Qwen realtime voice with the Nav2 backend."""

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration


def generate_launch_description() -> LaunchDescription:
    source = PythonLaunchDescriptionSource(
        get_package_share_directory("project_link_qwen_realtime_voice")
        + "/launch/qwen_realtime_voice.launch.py"
    )
    return LaunchDescription([
        IncludeLaunchDescription(
            source,
            launch_arguments={
                "navigation_backend": "nav2",
                "enable_motion": LaunchConfiguration("enable_motion", default="false"),
                "enable_visual_grasp": LaunchConfiguration("enable_visual_grasp", default="false"),
            }.items(),
        )
    ])
