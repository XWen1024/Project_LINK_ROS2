"""Launch the safe pure-voice Volcengine WebSocket S2S integration."""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description() -> LaunchDescription:
    default_params = os.path.join(
        get_package_share_directory("project_link_voice"),
        "config",
        "volc_s2s_voice.yaml",
    )
    params_file = LaunchConfiguration("params_file")
    keyboard_wakeup = LaunchConfiguration("keyboard_wakeup")
    wakeup_serial_port = LaunchConfiguration("wakeup_serial_port")
    audio_input_device_index = LaunchConfiguration("audio_input_device_index")
    audio_output_device_index = LaunchConfiguration("audio_output_device_index")
    native_bridge_executable = LaunchConfiguration("native_bridge_executable")
    pulse_sink = LaunchConfiguration("pulse_sink")
    return LaunchDescription(
        [
            DeclareLaunchArgument("params_file", default_value=default_params),
            DeclareLaunchArgument("keyboard_wakeup", default_value="false"),
            DeclareLaunchArgument("wakeup_serial_port", default_value="auto"),
            DeclareLaunchArgument("audio_input_device_index", default_value="0"),
            DeclareLaunchArgument("audio_output_device_index", default_value="-1"),
            DeclareLaunchArgument(
                "native_bridge_executable",
                default_value=os.environ.get(
                    "PROJECT_LINK_VOLC_BRIDGE_BIN",
                    "/home/wte/wheeltec_robot/experiments/volc_s2s_smoke/build/volc_ws_bridge",
                ),
            ),
            DeclareLaunchArgument(
                "pulse_sink",
                default_value="alsa_output.usb-C-Media_Electronics_Inc._USB_Audio_Device-00.analog-stereo",
            ),
            Node(
                package="project_link_voice",
                executable="volc_s2s_voice_node",
                name="volc_s2s_voice_node",
                output="screen",
                parameters=[
                    params_file,
                    {
                        "keyboard_wakeup": keyboard_wakeup,
                        "wakeup_serial_port": ParameterValue(wakeup_serial_port, value_type=str),
                        "audio_input_device_index": audio_input_device_index,
                        "audio_output_device_index": audio_output_device_index,
                        "native_bridge_executable": native_bridge_executable,
                        "pulse_sink": pulse_sink,
                    },
                ],
            ),
        ]
    )

