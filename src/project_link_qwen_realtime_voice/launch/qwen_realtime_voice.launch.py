"""Launch the independent Qwen realtime voice node."""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description() -> LaunchDescription:
    default_params = os.path.join(
        get_package_share_directory("project_link_qwen_realtime_voice"),
        "config",
        "qwen_realtime_voice.yaml",
    )
    params_file = LaunchConfiguration("params_file")
    navigation_backend = LaunchConfiguration("navigation_backend")
    return LaunchDescription([
        DeclareLaunchArgument("params_file", default_value=default_params),
        DeclareLaunchArgument("navigation_backend", default_value="nav2"),
        DeclareLaunchArgument("enable_audio", default_value="true"),
        DeclareLaunchArgument("enable_motion", default_value="false"),
        DeclareLaunchArgument("enable_visual_grasp", default_value="false"),
        DeclareLaunchArgument("enable_demo_motion", default_value="false"),
        DeclareLaunchArgument("pure_test_mode", default_value="auto"),
        DeclareLaunchArgument("keyboard_wakeup", default_value="false"),
        DeclareLaunchArgument("wakeup_serial_port", default_value="/dev/project_link_wakeup"),
        DeclareLaunchArgument("waypoints_override_file", default_value="~/.ros/project_link_voice/waypoints.json"),
        Node(
            package="project_link_qwen_realtime_voice",
            executable="qwen_realtime_voice_node",
            name="qwen_realtime_voice_node",
            output="screen",
            parameters=[
                params_file,
                {
                    "navigation_backend": ParameterValue(navigation_backend, value_type=str),
                    "enable_audio": LaunchConfiguration("enable_audio"),
                    "enable_motion": LaunchConfiguration("enable_motion"),
                    "enable_visual_grasp": LaunchConfiguration("enable_visual_grasp"),
                    "enable_demo_motion": LaunchConfiguration("enable_demo_motion"),
                    "pure_test_mode": ParameterValue(LaunchConfiguration("pure_test_mode"), value_type=str),
                    "keyboard_wakeup": LaunchConfiguration("keyboard_wakeup"),
                    "wakeup_serial_port": LaunchConfiguration("wakeup_serial_port"),
                    "waypoints_override_file": LaunchConfiguration("waypoints_override_file"),
                },
            ],
        ),
    ])
