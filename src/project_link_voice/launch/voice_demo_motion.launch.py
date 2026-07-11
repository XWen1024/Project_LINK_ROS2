"""Launch voice-only bounded /cmd_vel demo mode without SLAM or waypoints."""

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
    keyboard_wakeup = LaunchConfiguration("keyboard_wakeup")
    wakeup_serial_port = LaunchConfiguration("wakeup_serial_port")
    wakeup_serial_baud = LaunchConfiguration("wakeup_serial_baud")
    wakeup_match_text = LaunchConfiguration("wakeup_match_text")
    demo_linear_mps = LaunchConfiguration("demo_linear_mps")
    demo_angular_rps = LaunchConfiguration("demo_angular_rps")
    return LaunchDescription([
        DeclareLaunchArgument("params_file", default_value=default_params),
        DeclareLaunchArgument("enable_audio", default_value="true"),
        DeclareLaunchArgument("keyboard_wakeup", default_value="true"),
        DeclareLaunchArgument("wakeup_serial_port", default_value="/dev/ttyUSB0"),
        DeclareLaunchArgument("wakeup_serial_baud", default_value="115200"),
        DeclareLaunchArgument("wakeup_match_text", default_value="aiui_event"),
        DeclareLaunchArgument("demo_linear_mps", default_value="0.06"),
        DeclareLaunchArgument("demo_angular_rps", default_value="0.30"),
        Node(
            package="project_link_voice",
            executable="voice_dialog_node",
            name="voice_dialog_node",
            output="screen",
            parameters=[
                params_file,
                {
                    "enable_motion": False,
                    "enable_demo_motion": True,
                    "enable_audio": enable_audio,
                    "enable_llm_tools": False,
                    "enable_visual_grasp": False,
                    "wakeup_only": False,
                    "pure_test_mode": "on",
                    "keyboard_wakeup": keyboard_wakeup,
                    "wakeup_serial_port": wakeup_serial_port,
                    "wakeup_serial_baud": wakeup_serial_baud,
                    "wakeup_match_text": wakeup_match_text,
                    "demo_linear_mps": demo_linear_mps,
                    "demo_angular_rps": demo_angular_rps,
                },
            ],
        ),
    ])
