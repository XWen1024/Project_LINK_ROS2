"""Standalone LLM + TTS bounded motion demo without SLAM, waypoints, or arm."""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    enable_audio = LaunchConfiguration("enable_audio")
    keyboard_wakeup = LaunchConfiguration("keyboard_wakeup")
    wakeup_serial_port = LaunchConfiguration("wakeup_serial_port")
    wakeup_serial_baud = LaunchConfiguration("wakeup_serial_baud")
    wakeup_match_text = LaunchConfiguration("wakeup_match_text")
    audio_input_device_index = LaunchConfiguration("audio_input_device_index")
    demo_linear_mps = LaunchConfiguration("demo_linear_mps")
    demo_angular_rps = LaunchConfiguration("demo_angular_rps")
    return LaunchDescription([
        DeclareLaunchArgument("enable_audio", default_value="true"),
        DeclareLaunchArgument("keyboard_wakeup", default_value="false"),
        DeclareLaunchArgument("wakeup_serial_port", default_value="auto"),
        DeclareLaunchArgument("wakeup_serial_baud", default_value="115200"),
        DeclareLaunchArgument("wakeup_match_text", default_value="aiui_event"),
        DeclareLaunchArgument("audio_input_device_index", default_value="-1"),
        DeclareLaunchArgument("demo_linear_mps", default_value="0.06"),
        DeclareLaunchArgument("demo_angular_rps", default_value="0.30"),
        Node(
            package="project_link_voice",
            executable="llm_motion_demo_node",
            name="llm_motion_demo_node",
            output="screen",
            parameters=[
                {
                    "enable_audio": enable_audio,
                    "keyboard_wakeup": keyboard_wakeup,
                    "wakeup_serial_port": wakeup_serial_port,
                    "wakeup_serial_baud": wakeup_serial_baud,
                    "wakeup_match_text": wakeup_match_text,
                    "audio_input_device_index": audio_input_device_index,
                    "demo_linear_mps": demo_linear_mps,
                    "demo_angular_rps": demo_angular_rps,
                }
            ],
        ),
    ])
