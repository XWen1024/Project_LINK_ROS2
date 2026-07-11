"""Launch only the voice service for local wakeup/audio testing."""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    default_params = os.path.join(
        get_package_share_directory("project_link_voice"), "config", "voice_direct_drive.yaml"
    )
    params_file = LaunchConfiguration("params_file")
    enable_audio = LaunchConfiguration("enable_audio")
    wakeup_only = LaunchConfiguration("wakeup_only")
    keyboard_wakeup = LaunchConfiguration("keyboard_wakeup")
    wakeup_serial_port = LaunchConfiguration("wakeup_serial_port")
    wakeup_serial_baud = LaunchConfiguration("wakeup_serial_baud")
    wakeup_match_text = LaunchConfiguration("wakeup_match_text")
    return LaunchDescription([
        DeclareLaunchArgument("params_file", default_value=default_params),
        DeclareLaunchArgument("enable_audio", default_value="true"),
        DeclareLaunchArgument("wakeup_only", default_value="true"),
        DeclareLaunchArgument("keyboard_wakeup", default_value="false"),
        DeclareLaunchArgument("wakeup_serial_port", default_value="COM9"),
        DeclareLaunchArgument("wakeup_serial_baud", default_value="115200"),
        DeclareLaunchArgument("wakeup_match_text", default_value="aiui_event"),
        Node(
            package="project_link_voice",
            executable="voice_tts_node",
            name="voice_tts_node",
            output="screen",
            parameters=[params_file],
        ),
        Node(
            package="project_link_voice",
            executable="voice_dialog_node",
            name="voice_dialog_node",
            output="screen",
            parameters=[
                params_file,
                {
                    "enable_motion": False,
                    "enable_audio": enable_audio,
                    "wakeup_only": wakeup_only,
                    "pure_test_mode": "on",
                    "keyboard_wakeup": keyboard_wakeup,
                    "wakeup_serial_port": wakeup_serial_port,
                    "wakeup_serial_baud": wakeup_serial_baud,
                    "wakeup_match_text": wakeup_match_text,
                },
            ],
        ),
    ])
