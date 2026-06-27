"""Project LINK Point-LIO entrypoint for Unitree L1 / UniLidar.

Phase A runs 3D lidar-inertial odometry only:
  /unilidar/cloud + /unilidar/imu -> Point-LIO -> /odom_lio and odom -> base_footprint

Phase B can additionally run slam_toolbox with enable_slam_toolbox:=true:
  /scan + Point-LIO TF -> slam_toolbox -> /map and map -> odom
"""

import os

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    workspace = os.environ.get("PROJECT_LINK_WORKSPACE", "/home/wte/wheeltec_robot")
    default_config = os.path.join(
        workspace,
        "configs",
        "point_lio",
        "unilidar_l1_project_link.yaml",
    )

    config_file = LaunchConfiguration("config_file")
    odom_only = LaunchConfiguration("odom_only")
    enable_slam_toolbox = LaunchConfiguration("enable_slam_toolbox")
    use_imu_as_input = LaunchConfiguration("use_imu_as_input")

    point_lio_node = Node(
        package="point_lio",
        executable="pointlio_mapping",
        name="point_lio_mapping",
        output="screen",
        parameters=[
            config_file,
            {
                "use_imu_as_input": use_imu_as_input,
                "prop_at_freq_of_imu": True,
                "check_satu": True,
                "init_map_size": 10,
                "point_filter_num": 1,
                "space_down_sample": True,
                "filter_size_surf": 0.1,
                "filter_size_map": 0.1,
                "cube_side_length": 1000.0,
                "runtime_pos_log_enable": False,
                "odom_only": odom_only,
                "odom_header_frame_id": "odom",
                "odom_child_frame_id": "base_footprint",
            },
        ],
        remappings=[
            ("/aft_mapped_to_init", "/odom_lio"),
            ("/odom_corrected", "/odom_lio"),
            ("/cloud_registered", "/point_lio/cloud_registered"),
            ("/cloud_registered_body", "/point_lio/cloud_registered_body"),
            ("/cloud_effected", "/point_lio/cloud_effected"),
            ("/Laser_map", "/point_lio/laser_map"),
            ("/path", "/path_lio"),
        ],
    )

    slam_toolbox_node = Node(
        package="slam_toolbox",
        executable="async_slam_toolbox_node",
        name="slam_toolbox",
        output="screen",
        condition=IfCondition(enable_slam_toolbox),
        parameters=[
            {
                "scan_topic": "/scan",
                "odom_frame": "odom",
                "map_frame": "map",
                "base_frame": "base_footprint",
                "mode": "mapping",
                "resolution": 0.05,
                "map_update_interval": 2.0,
                "max_laser_range": 20.0,
                "minimum_travel_distance": 0.3,
                "minimum_travel_heading": 0.3,
                "scan_buffer_size": 20,
                "scan_buffer_maximum_scan_distance": 10.0,
                "link_match_minimum_response_fine": 0.45,
                "link_scan_maximum_distance": 2.5,
                "loop_search_maximum_distance": 3.0,
                "do_loop_closing": True,
                "loop_match_minimum_chain_size": 10,
                "loop_match_maximum_variance_coarse": 3.0,
                "loop_match_minimum_response_coarse": 0.35,
                "loop_match_minimum_response_fine": 0.45,
                "solver_plugin": "solver_plugins::CeresSolver",
                "ceres_linear_solver": "SPARSE_NORMAL_CHOLESKY",
                "ceres_preconditioner": "SCHUR_JACOBI",
                "ceres_trust_strategy": "LEVENBERG_MARQUARDT",
                "ceres_dogleg_type": "TRADITIONAL_DOGLEG",
                "ceres_loss_function": "None",
            }
        ],
    )

    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "config_file",
                default_value=default_config,
                description="Point-LIO Unitree L1 config file.",
            ),
            DeclareLaunchArgument(
                "odom_only",
                default_value="false",
                description=(
                    "If true, Point-LIO suppresses registered cloud/path output and "
                    "only publishes odometry. Keep false for RViz 3D inspection."
                ),
            ),
            DeclareLaunchArgument(
                "enable_slam_toolbox",
                default_value="false",
                description="Run slam_toolbox on /scan using Point-LIO TF.",
            ),
            DeclareLaunchArgument(
                "use_imu_as_input",
                default_value="false",
                description="Point-LIO algorithm switch; keep false for first Unitree L1 pass.",
            ),
            point_lio_node,
            slam_toolbox_node,
        ]
    )
