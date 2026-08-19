"""Project LINK Point-LIO entrypoint for Unitree L1 / UniLidar.

Phase A preserves Point-LIO's 3D pose and derives a planar base pose:
  /unilidar/cloud + /unilidar/imu
  -> Point-LIO /odom_lio_raw and lio_odom -> lio_base
  -> lio_planar_projection /odom_lio and odom -> base_footprint

Phase B can additionally run slam_toolbox with enable_slam_toolbox:=true:
  /scan_accumulated + Point-LIO TF -> slam_toolbox -> /map and map -> odom
"""

import os

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    workspace = os.environ.get("PROJECT_LINK_WORKSPACE", "/home/wte/wheeltec_robot")
    default_config = os.path.join(
        workspace,
        "configs",
        "point_lio",
        "unilidar_l1_project_link.yaml",
    )
    default_projection_config = os.path.join(
        workspace,
        "configs",
        "point_lio",
        "lio_planar_projection.yaml",
    )

    config_file = LaunchConfiguration("config_file")
    projection_config_file = LaunchConfiguration("projection_config_file")
    odom_only = LaunchConfiguration("odom_only")
    point_filter_num = LaunchConfiguration("point_filter_num")
    filter_size_surf = LaunchConfiguration("filter_size_surf")
    filter_size_map = LaunchConfiguration("filter_size_map")
    cube_side_length = LaunchConfiguration("cube_side_length")
    det_range = LaunchConfiguration("det_range")
    enable_slam_toolbox = LaunchConfiguration("enable_slam_toolbox")
    use_imu_as_input = LaunchConfiguration("use_imu_as_input")
    lidar_mount_yaw_rad = LaunchConfiguration("lidar_mount_yaw_rad")
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
                "point_filter_num": point_filter_num,
                "space_down_sample": True,
                "filter_size_surf": filter_size_surf,
                "filter_size_map": filter_size_map,
                "cube_side_length": cube_side_length,
                "mapping.det_range": det_range,
                "runtime_pos_log_enable": False,
                "odom_only": odom_only,
                "odom_header_frame_id": "lio_odom",
                "odom_child_frame_id": "lio_base",
            },
        ],
        remappings=[
            ("/aft_mapped_to_init", "/odom_lio_raw"),
            ("/odom_corrected", "/odom_lio_raw"),
            ("/cloud_registered", "/point_lio/cloud_registered"),
            ("/cloud_registered_body", "/point_lio/cloud_registered_body"),
            ("/cloud_effected", "/point_lio/cloud_effected"),
            ("/Laser_map", "/point_lio/laser_map"),
            ("/path", "/path_lio"),
        ],
    )

    lio_planar_projection_node = Node(
        package="turn_on_wheeltec_robot",
        executable="lio_planar_projection",
        name="lio_planar_projection",
        output="screen",
        parameters=[
            projection_config_file,
            {
                "lidar_mount_yaw_rad": ParameterValue(
                    lidar_mount_yaw_rad, value_type=float
                )
            },
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
                "scan_topic": "/scan_accumulated",
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
                "projection_config_file",
                default_value=default_projection_config,
                description="Planar Point-LIO base projection configuration.",
            ),
            DeclareLaunchArgument(
                "lidar_mount_yaw_rad",
                default_value="3.14159",
                description="Calibrated chassis-to-lidar mounting yaw in radians.",
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
                "point_filter_num",
                default_value="2",
                description="Keep one point out of every N input points.",
            ),
            DeclareLaunchArgument(
                "filter_size_surf",
                default_value="0.15",
                description="Surface voxel size in metres.",
            ),
            DeclareLaunchArgument(
                "filter_size_map",
                default_value="0.15",
                description="Map voxel size in metres.",
            ),
            DeclareLaunchArgument(
                "cube_side_length",
                default_value="150.0",
                description="Local Point-LIO map cube side length in metres.",
            ),
            DeclareLaunchArgument(
                "det_range",
                default_value="40.0",
                description="Point-LIO detection range in metres.",
            ),
            DeclareLaunchArgument(
                "enable_slam_toolbox",
                default_value="false",
                description="Run slam_toolbox on /scan_accumulated using Point-LIO TF.",
            ),
            DeclareLaunchArgument(
                "use_imu_as_input",
                default_value="false",
                description="Point-LIO algorithm switch; keep false for first Unitree L1 pass.",
            ),
            point_lio_node,
            lio_planar_projection_node,
            slam_toolbox_node,
        ]
    )
