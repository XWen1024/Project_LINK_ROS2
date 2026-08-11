"""Launch guarded voice dialog and direct-drive action server without Nav2."""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description() -> LaunchDescription:
    default_params = os.path.join(
        get_package_share_directory("project_link_voice"), "config", "voice_direct_drive.yaml"
    )
    params_file = LaunchConfiguration("params_file")
    enable_motion = LaunchConfiguration("enable_motion")
    enable_audio = LaunchConfiguration("enable_audio")
    enable_llm_tools = LaunchConfiguration("enable_llm_tools")
    enable_visual_grasp = LaunchConfiguration("enable_visual_grasp")
    pure_test_mode = LaunchConfiguration("pure_test_mode")
    waypoints_override_file = LaunchConfiguration("waypoints_override_file")
    wakeup_serial_port = LaunchConfiguration("wakeup_serial_port")
    audio_input_device_index = LaunchConfiguration("audio_input_device_index")
    return LaunchDescription([
        DeclareLaunchArgument("params_file", default_value=default_params),
        DeclareLaunchArgument(
            "enable_motion",
            default_value="false",
            description="Explicitly permit `/cmd_vel` publishing after voice confirmation.",
        ),
        DeclareLaunchArgument("enable_audio", default_value="true"),
        DeclareLaunchArgument("enable_llm_tools", default_value="true"),
        DeclareLaunchArgument("pure_test_mode", default_value="auto"),
        DeclareLaunchArgument("waypoints_override_file", default_value=""),
        DeclareLaunchArgument("wakeup_serial_port", default_value="auto"),
        DeclareLaunchArgument("audio_input_device_index", default_value="0"),
        DeclareLaunchArgument(
            "enable_visual_grasp",
            default_value="false",
            description="Permit arm prepare and TrackAndGrasp after a successful direct-drive arrival.",
        ),
        Node(
            package="project_link_voice",
            executable="ab_drive_server",
            name="ab_drive_server",
            output="screen",
            parameters=[params_file, {"enable_motion": enable_motion}],
        ),
        Node(
            package="project_link_voice",
            executable="voice_dialog_node",
            name="voice_dialog_node",
            output="screen",
            parameters=[
                params_file,
                {
                    "enable_motion": enable_motion,
                    "enable_audio": enable_audio,
                    "enable_llm_tools": enable_llm_tools,
                    "enable_visual_grasp": enable_visual_grasp,
                    "pure_test_mode": ParameterValue(pure_test_mode, value_type=str),
                    "waypoints_override_file": waypoints_override_file,
                    "navigation_backend": "direct_drive",
                    "wakeup_serial_port": wakeup_serial_port,
                    "audio_input_device_index": audio_input_device_index,
                },
            ],
        ),
    ])
