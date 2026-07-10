"""rf2o + C63A wheel odom EKF, without slam_toolbox.

Use this launch during Nav2 testing after a map has already been saved. It keeps
ownership of odom -> base_footprint in robot_localization and leaves map -> odom
for the localization stack used by Nav2.
"""

from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    rf2o_node = Node(
        package="rf2o_laser_odometry",
        executable="rf2o_laser_odometry_node",
        name="rf2o_laser_odometry",
        output="screen",
        parameters=[
            {
                "laser_scan_topic": "/scan",
                "odom_topic": "/odom_rf2o",
                "publish_tf": False,
                "base_frame_id": "base_footprint",
                "odom_frame_id": "odom",
                "init_pose_from_topic": "",
                "freq": 10.0,
            }
        ],
    )

    ekf_fusion_node = Node(
        package="robot_localization",
        executable="ekf_node",
        name="ekf_odom_fusion",
        output="screen",
        parameters=[
            {
                "map_frame": "map",
                "odom_frame": "odom",
                "base_link_frame": "base_footprint",
                "world_frame": "odom",
                "odom0": "/odom",
                "odom0_config": [
                    True,
                    True,
                    False,
                    False,
                    False,
                    True,
                    True,
                    False,
                    False,
                    False,
                    False,
                    True,
                    False,
                    False,
                    False,
                ],
                "odom0_differential": False,
                "odom0_relative": False,
                "odom1": "/odom_rf2o",
                "odom1_config": [
                    True,
                    True,
                    False,
                    False,
                    False,
                    True,
                    True,
                    False,
                    False,
                    False,
                    False,
                    True,
                    False,
                    False,
                    False,
                ],
                "odom1_differential": False,
                "odom1_relative": False,
                "frequency": 20.0,
                "sensor_timeout": 0.1,
                "two_d_mode": True,
                "publish_tf": True,
                "publish_acceleration": False,
                "smooth_lagged_data": True,
                "history_length": 0.5,
            }
        ],
    )

    return LaunchDescription([rf2o_node, ekf_fusion_node])
