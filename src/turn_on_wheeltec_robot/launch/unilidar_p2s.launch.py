from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    lidar_tf_x = LaunchConfiguration("lidar_tf_x")
    lidar_tf_y = LaunchConfiguration("lidar_tf_y")
    lidar_tf_z = LaunchConfiguration("lidar_tf_z")
    lidar_tf_roll = LaunchConfiguration("lidar_tf_roll")
    lidar_tf_pitch = LaunchConfiguration("lidar_tf_pitch")
    lidar_tf_yaw = LaunchConfiguration("lidar_tf_yaw")

    return LaunchDescription(
        [
            DeclareLaunchArgument("lidar_tf_x", default_value="0"),
            DeclareLaunchArgument("lidar_tf_y", default_value="0"),
            DeclareLaunchArgument("lidar_tf_z", default_value="0"),
            DeclareLaunchArgument("lidar_tf_roll", default_value="3.14159"),
            DeclareLaunchArgument("lidar_tf_pitch", default_value="0.0"),
            DeclareLaunchArgument("lidar_tf_yaw", default_value="0.44041"),
            Node(
                package="tf2_ros",
                executable="static_transform_publisher",
                name="unilidar_tf_broadcaster",
                arguments=[
                    "--x",
                    lidar_tf_x,
                    "--y",
                    lidar_tf_y,
                    "--z",
                    lidar_tf_z,
                    "--roll",
                    lidar_tf_roll,
                    "--pitch",
                    lidar_tf_pitch,
                    "--yaw",
                    lidar_tf_yaw,
                    "--frame-id",
                    "unilidar_link",
                    "--child-frame-id",
                    "unilidar_lidar",
                ],
            ),
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
        ]
    )
