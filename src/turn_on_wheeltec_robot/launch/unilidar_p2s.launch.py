from launch import LaunchDescription
from launch_ros.actions import Node

def generate_launch_description():
    return LaunchDescription([
        # 将驱动发布的坐标系 unilidar_lidar 对齐到 URDF 骨架上的 unilidar_link
        # roll: 3.14159  -> 补偿雷达倒装安装（上下翻转）
        # yaw:  0.44041  -> 补偿雷达安装的水平朝向偏转（实车盲调值，勿改）
        Node(
            package='tf2_ros',
            executable='static_transform_publisher',
            name='unilidar_tf_broadcaster',
            arguments=[
                '--x', '0', '--y', '0', '--z', '0',
                '--roll', '3.14159', '--pitch', '0.0', '--yaw', '0.44041',
                '--frame-id', 'unilidar_link', '--child-frame-id', 'unilidar_lidar'
            ]
        ),
        # 将 3D 点云切片成 2D /scan，高度窗口 0.45m~0.65m（车身中段障碍物高度）
        Node(
            package='pointcloud_to_laserscan',
            executable='pointcloud_to_laserscan_node',
            name='pointcloud_to_laserscan',
            remappings=[
                ('cloud_in', '/unilidar/cloud'),
                ('scan', '/scan')
            ],
            parameters=[{
                'target_frame': 'base_link',
                'transform_tolerance': 0.01,
                'min_height': 0.45,
                'max_height': 0.65,
                'angle_min': -3.14159,
                'angle_max':  3.14159,
                'angle_increment': 0.0087,
                'queue_size': 10,
                'scan_time': 0.1,
                'range_min': 0.1,
                'range_max': 30.0,
                'use_inf': True,
                'inf_epsilon': 1.0,
                'reliability': 'best_effort'
            }]
        )
    ])