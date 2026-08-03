"""Start Nav2 against the already-running Point-LIO Phase B mapping stack.

This launch intentionally starts no lidar driver, robot description, odometry,
AMCL, map server, or slam_toolbox. The required pose chain and live OccupancyGrid
remain owned by Point-LIO Phase B:

  slam_toolbox map -> odom
  lio_planar_projection odom -> base_footprint
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration


def generate_launch_description():
    nav2_bringup_dir = get_package_share_directory("nav2_bringup")
    default_params = os.path.join(
        get_package_share_directory("wheeltec_nav2"),
        "param",
        "wheeltec_params",
        "param_point_lio_navigation.yaml",
    )

    use_sim_time = LaunchConfiguration("use_sim_time")
    params_file = LaunchConfiguration("params_file")
    autostart = LaunchConfiguration("autostart")
    use_respawn = LaunchConfiguration("use_respawn")
    log_level = LaunchConfiguration("log_level")

    return LaunchDescription(
        [
            DeclareLaunchArgument("use_sim_time", default_value="false"),
            DeclareLaunchArgument("params_file", default_value=default_params),
            DeclareLaunchArgument("autostart", default_value="true"),
            DeclareLaunchArgument("use_respawn", default_value="false"),
            DeclareLaunchArgument("log_level", default_value="info"),
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(
                    os.path.join(nav2_bringup_dir, "launch", "navigation_launch.py")
                ),
                launch_arguments={
                    "use_sim_time": use_sim_time,
                    "params_file": params_file,
                    "autostart": autostart,
                    "use_composition": "False",
                    "use_respawn": use_respawn,
                    "log_level": log_level,
                }.items(),
            ),
        ]
    )
