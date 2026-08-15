import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    default_config = os.path.join(
        get_package_share_directory("project_link_vl53l0x"),
        "config",
        "vl53l0x_gripper.yaml",
    )
    return LaunchDescription([
        DeclareLaunchArgument("config", default_value=default_config),
        Node(
            package="project_link_vl53l0x",
            executable="serial_range_node",
            name="vl53l0x_serial_range_node",
            output="screen",
            parameters=[LaunchConfiguration("config")],
        ),
    ])
