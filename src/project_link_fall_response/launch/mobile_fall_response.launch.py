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
    return LaunchDescription([
        DeclareLaunchArgument("config", default_value=default_config),
        Node(
            package="project_link_fall_response",
            executable="mobile_fall_coordinator",
            name="mobile_fall_coordinator",
            output="screen",
            parameters=[config],
        ),
        Node(
            package="project_link_fall_response",
            executable="fall_http_gateway",
            name="fall_http_gateway",
            output="screen",
        ),
    ])
