"""Start BU04 ingestion and guarded UWB-to-Nav2 person navigation."""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import EnvironmentVariable, LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    package_dir = get_package_share_directory("project_link_uwb_navigation")
    default_config = os.path.join(package_dir, "config", "uwb_navigation.yaml")
    params_file = LaunchConfiguration("params_file")
    enable_motion = LaunchConfiguration("enable_motion")
    serial_device = LaunchConfiguration("serial_device")
    tag_address = LaunchConfiguration("tag_address")

    return LaunchDescription(
        [
            DeclareLaunchArgument("params_file", default_value=default_config),
            DeclareLaunchArgument("enable_motion", default_value="false"),
            DeclareLaunchArgument(
                "serial_device",
                default_value=EnvironmentVariable("PROJECT_LINK_UWB_DEVICE", default_value=""),
            ),
            DeclareLaunchArgument(
                "tag_address",
                default_value=EnvironmentVariable("PROJECT_LINK_UWB_TAG_ADDRESS", default_value=""),
            ),
            Node(
                package="project_link_uwb_navigation",
                executable="uwb_serial_node",
                name="uwb_serial_node",
                output="screen",
                parameters=[
                    params_file,
                    {"device": serial_device, "tag_address": tag_address},
                ],
            ),
            Node(
                package="project_link_uwb_navigation",
                executable="uwb_nav2_server",
                name="uwb_nav2_server",
                output="screen",
                parameters=[params_file, {"enable_motion": enable_motion}],
            ),
        ]
    )
