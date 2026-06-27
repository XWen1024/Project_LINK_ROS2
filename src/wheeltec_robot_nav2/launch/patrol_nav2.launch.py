"""
巡检车导航启动文件
定位方案：slam_toolbox localization 模式（替代 AMCL）
导航方案：Nav2（bt_navigator + controller + planner + costmap）
使用方式：先完成建图并保存地图，再运行此文件
"""
import os
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    use_sim_time = LaunchConfiguration('use_sim_time', default='false')

    # 地图文件路径（pgm+yaml，由 map_saver 保存的格式，供 map_server 加载）
    map_yaml_file = LaunchConfiguration(
        'map',
        default='/home/wte/maps/patrol_map.yaml'
    )

    # slam_toolbox 序列化地图路径（不含扩展名，供 slam_toolbox 定位模式加载）
    slam_map_file = LaunchConfiguration(
        'slam_map',
        default='/home/wte/maps/patrol_map'
    )

    nav2_params_file = LaunchConfiguration(
        'params_file',
        default=os.path.join(
            get_package_share_directory('wheeltec_nav2'),
            'param', 'wheeltec_params', 'param_patrol_nav2.yaml'
        )
    )

    slam_toolbox_config = os.path.join(
        get_package_share_directory('wheeltec_slam_toolbox'),
        'config', 'mapper_params_localization.yaml'
    )
    nav2_bringup_dir = get_package_share_directory('nav2_bringup')

    return LaunchDescription([
        DeclareLaunchArgument('use_sim_time', default_value='false'),
        DeclareLaunchArgument('map',      default_value=map_yaml_file),
        DeclareLaunchArgument('slam_map', default_value=slam_map_file),
        DeclareLaunchArgument('params_file', default_value=nav2_params_file),

        # --------------------------------------------------------
        # 1. map_server：加载静态地图供全局代价地图使用
        # --------------------------------------------------------
        Node(
            package='nav2_map_server',
            executable='map_server',
            name='map_server',
            output='screen',
            parameters=[{
                'use_sim_time': use_sim_time,
                'yaml_filename': map_yaml_file,
            }]
        ),

        # --------------------------------------------------------
        # 2. slam_toolbox 定位模式：发布 map->odom TF
        #    替代 AMCL，基于激光扫描匹配定位，不依赖粒子滤波
        # --------------------------------------------------------
        Node(
            package='slam_toolbox',
            executable='localization_slam_toolbox_node',
            name='slam_toolbox',
            output='screen',
            parameters=[slam_toolbox_config],
        ),

        # --------------------------------------------------------
        # 3. lifecycle_manager：管理 map_server 的生命周期
        # --------------------------------------------------------
        Node(
            package='nav2_lifecycle_manager',
            executable='lifecycle_manager',
            name='lifecycle_manager_map',
            output='screen',
            parameters=[{
                'use_sim_time': use_sim_time,
                'autostart': True,
                'node_names': ['map_server'],
            }]
        ),

        # --------------------------------------------------------
        # 4. Nav2 核心导航栈（不含 amcl 和 map_server）
        # --------------------------------------------------------
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(
                os.path.join(nav2_bringup_dir, 'launch', 'navigation_launch.py')
            ),
            launch_arguments={
                'use_sim_time': use_sim_time,
                'params_file':  nav2_params_file,
                'autostart':    'true',
            }.items(),
        ),
    ])
