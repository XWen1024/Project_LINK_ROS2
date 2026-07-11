import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    default_config = os.path.join(
        get_package_share_directory("project_link_fall_response"),
        "config",
        "fall_response.yaml",
    )
    config = LaunchConfiguration("config")
    camera_device = LaunchConfiguration("camera_device")
    return LaunchDescription([
        DeclareLaunchArgument("config", default_value=default_config),
        DeclareLaunchArgument("camera_device", default_value="/dev/FallCam"),
        Node(
            package="project_link_fall_response",
            executable="fall_camera_node",
            name="fall_camera_node",
            output="screen",
            parameters=[config, {"camera_device": camera_device}],
        ),
        Node(
            package="project_link_fall_response",
            executable="fall_response_node",
            name="fall_response_node",
            output="screen",
            parameters=[config],
        ),
    ])
