import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    accumulator_config = os.path.join(
        get_package_share_directory("turn_on_wheeltec_robot"),
        "config",
        "laser_scan_accumulator.yaml",
    )

    return LaunchDescription(
        [
            Node(
                package="pointcloud_to_laserscan",
                executable="pointcloud_to_laserscan_node",
                name="pointcloud_to_laserscan",
                remappings=[
                    ("cloud_in", "/unilidar/cloud"),
                    ("scan", "/scan"),
                ],
                parameters=[
                    {
                        "target_frame": "base_link",
                        "transform_tolerance": 0.01,
                        "min_height": 0.45,
                        "max_height": 0.65,
                        "angle_min": -3.14159,
                        "angle_max": 3.14159,
                        "angle_increment": 0.0087,
                        "queue_size": 10,
                        "scan_time": 0.1,
                        "range_min": 0.1,
                        "range_max": 30.0,
                        "use_inf": True,
                        "inf_epsilon": 1.0,
                        "reliability": "best_effort",
                    }
                ],
            ),
            Node(
                package="turn_on_wheeltec_robot",
                executable="laser_scan_accumulator",
                name="laser_scan_accumulator",
                output="screen",
                parameters=[accumulator_config],
            ),
        ]
    )
