import os
from pathlib import Path
import launch
from launch.actions import SetEnvironmentVariable
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (DeclareLaunchArgument, GroupAction,
                            IncludeLaunchDescription, SetEnvironmentVariable)
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PythonExpression
from launch_ros.actions import PushRosNamespace
import launch_ros.actions
from launch.conditions import IfCondition
from launch.conditions import UnlessCondition


def generate_launch_description():
    bringup_dir = get_package_share_directory('turn_on_wheeltec_robot')
    launch_dir = os.path.join(bringup_dir, 'launch')

    carto_slam = LaunchConfiguration('carto_slam', default='False')
    carto_slam_dec = DeclareLaunchArgument('carto_slam', default_value='False')

    robot_nav = LaunchConfiguration('robot_nav', default='False')
    robot_nav_dec = DeclareLaunchArgument('robot_nav', default_value='False')

    wheeltec_robot = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(launch_dir, 'base_serial.launch.py')),
    )

    # 修复：强制将 EKF 输出的 odom_frame_id 设为标准的 "odom"
    # 原来轮趣 EKF 默认发布 frame_id = "odom_combined"，导致 TF 树断链：
    #   map -> odom (正常) 但 odom_combined -> base_link (找不到)
    # 通过 launch_arguments 覆盖 EKF 配置里的 odom_frame_id
    robot_ekf = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(launch_dir, 'wheeltec_ekf.launch.py')),
        launch_arguments={
            'carto_slam': carto_slam,
            'robot_nav': robot_nav,
            # 强制 EKF 输出标准 odom 坐标系
            'odom_frame_id': 'odom',
            'base_link_frame': 'base_footprint',
        }.items(),
    )

    joint_state_publisher_node = launch_ros.actions.Node(
        package='joint_state_publisher',
        executable='joint_state_publisher',
        name='joint_state_publisher',
    )

    car_mode_type = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(launch_dir, 'robot_mode_description.launch.py')),
    )

    ld = LaunchDescription()
    ld.add_action(carto_slam_dec)
    ld.add_action(robot_nav_dec)
    ld.add_action(wheeltec_robot)
    ld.add_action(joint_state_publisher_node)
    ld.add_action(robot_ekf)
    ld.add_action(car_mode_type)
    return ld