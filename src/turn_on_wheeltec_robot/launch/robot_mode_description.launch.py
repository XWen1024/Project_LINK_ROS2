import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import Command, LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue

# ------------- Launch 描述 -------------
def generate_launch_description():
    default_xacro = os.path.join(
        get_package_share_directory("turn_on_wheeltec_robot"),
        "urdf",
        "patrol_robot.urdf.xacro",
    )
    urdf_xacro = LaunchConfiguration("urdf_xacro")
    lidar_mount_yaw_rad = LaunchConfiguration("lidar_mount_yaw_rad")

    return LaunchDescription([
        DeclareLaunchArgument(
            "urdf_xacro",
            default_value=default_xacro,
            description="Canonical Project LINK robot xacro file.",
        ),
        DeclareLaunchArgument(
            "lidar_mount_yaw_rad",
            default_value="3.14159",
            description="Calibrated chassis-to-lidar mounting yaw in radians.",
        ),
        Node(
            package="robot_state_publisher",
            executable="robot_state_publisher",
            name="robot_state_publisher",
            output="screen",
            parameters=[
                {
                    "robot_description": ParameterValue(
                        Command([
                            "xacro ",
                            urdf_xacro,
                            " lidar_mount_yaw_rad:=",
                            lidar_mount_yaw_rad,
                        ]),
                        value_type=str,
                    )
                }
            ],
        ),
    ])
