from launch import LaunchDescription
from launch_ros.actions import Node

def generate_launch_description():
    carto_config_dir = '/home/wte/wheeltec_robot/src/turn_on_wheeltec_robot/config'
    
    return LaunchDescription([
        # 1. Cartographer 核心建图节点
        Node(
            package='cartographer_ros',
            executable='cartographer_node',
            name='cartographer_node',
            output='screen',
            arguments=[
                '-configuration_directory', carto_config_dir,
                '-configuration_basename', 'unilidar_2d.lua'
            ],
            remappings=[
                ('scan', '/scan'),
		('imu', '/imu/data_raw')
            ]
        ),
        # 2. 栅格地图生成节点 (供 RViz2 显示和后续保存)
        Node(
            package='cartographer_ros',
            executable='cartographer_occupancy_grid_node',
            name='cartographer_occupancy_grid_node',
            output='screen',
            arguments=['-resolution', '0.05', '-publish_period_sec', '1.0']
        )
    ])
