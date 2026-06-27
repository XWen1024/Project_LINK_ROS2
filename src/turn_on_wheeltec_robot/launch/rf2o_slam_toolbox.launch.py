"""
新建图方案：rf2o_laser_odometry + slam_toolbox (async)
架构说明：
  /scan  →  rf2o_laser_odometry  →  /odom_rf2o (激光里程计，不依赖 IMU/编码器)
  /odom (wheel) + /odom_rf2o  →  robot_localization EKF  →  /odometry/filtered
  /scan + /odometry/filtered  →  slam_toolbox  →  /map
优势：
  - rf2o 完全基于激光帧间匹配，不受 IMU 抖动影响
  - slam_toolbox async 模式容忍激光帧率不稳定
  - EKF 融合双里程计，位姿估计更鲁棒
"""

import os
from launch import LaunchDescription
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():

    # ------------------------------------------------------------------ #
    # 1. rf2o 激光里程计节点
    #    纯激光帧间匹配推算里程，完全不依赖 IMU，规避宇树雷达旋转抖动问题
    # ------------------------------------------------------------------ #
    rf2o_node = Node(
        package='rf2o_laser_odometry',
        executable='rf2o_laser_odometry_node',
        name='rf2o_laser_odometry',
        output='screen',
        parameters=[{
            'laser_scan_topic': '/scan',         # 输入：2D 激光扫描
            'odom_topic': '/odom_rf2o',          # 输出：激光里程计
            'publish_tf': False,                 # 不发布 TF，由 EKF 统一管理
            'base_frame_id': 'base_footprint',
            'odom_frame_id': 'odom',
            'init_pose_from_topic': '',
            'freq': 10.0,                        # 与 pointcloud_to_laserscan scan_time 对齐
        }]
    )

    # ------------------------------------------------------------------ #
    # 2. robot_localization EKF：融合轮式里程计 + rf2o 激光里程计
    #    输出高质量的 /odometry/filtered，作为 slam_toolbox 的初始位姿
    # ------------------------------------------------------------------ #
    ekf_fusion_node = Node(
        package='robot_localization',
        executable='ekf_node',
        name='ekf_odom_fusion',
        output='screen',
        parameters=[{
            # 坐标系设置
            'map_frame':       'map',
            'odom_frame':      'odom',
            'base_link_frame': 'base_footprint',
            'world_frame':     'odom',

            # 融合源1：轮式里程计 /odom（来自底盘 EKF）
            'odom0': '/odom',
            # 融合内容：x, y, yaw 及其速度 [x,y,z, roll,pitch,yaw, vx,vy,vz, vroll,vpitch,vyaw, ax,ay,az]
            'odom0_config': [
                True,  True,  False,
                False, False, True,
                True,  False, False,
                False, False, True,
                False, False, False,
            ],
            'odom0_differential': False,
            'odom0_relative':     False,

            # 融合源2：rf2o 激光里程计 /odom_rf2o
            'odom1': '/odom_rf2o',
            'odom1_config': [
                True,  True,  False,
                False, False, True,
                True,  False, False,
                False, False, True,
                False, False, False,
            ],
            'odom1_differential': False,
            'odom1_relative':     False,

            # EKF 参数
            'frequency':             20.0,
            'sensor_timeout':        0.1,
            'two_d_mode':            True,
            'publish_tf':            True,    # 发布 odom -> base_footprint TF
            'publish_acceleration':  False,
            'smooth_lagged_data':    True,
            'history_length':        0.5,
        }]
    )

    # ------------------------------------------------------------------ #
    # 3. slam_toolbox 异步建图节点
    #    async 模式：不等待每帧都处理，对帧率抖动容忍度高
    # ------------------------------------------------------------------ #
    slam_toolbox_node = Node(
        package='slam_toolbox',
        executable='async_slam_toolbox_node',
        name='slam_toolbox',
        output='screen',
        parameters=[{
            # 话题与坐标系
            'scan_topic':       '/scan',
            'odom_frame':       'odom',
            'map_frame':        'map',
            'base_frame':       'base_footprint',

            # 模式：mapping（建图），localization（定位复用已有地图）
            'mode': 'mapping',

            # 建图分辨率（米）
            'resolution': 0.05,

            # 地图更新间隔（秒），适当拉长减少因抖动产生的地图噪点
            'map_update_interval': 2.0,

            # 最大激光扫描距离，与 pointcloud_to_laserscan 对齐
            'max_laser_range': 20.0,

            # 回环检测相关
            'minimum_travel_distance':  0.3,   # 移动多少米后才尝试回环
            'minimum_travel_heading':   0.3,   # 转多少弧度后才尝试回环
            'scan_buffer_size':         20,
            'scan_buffer_maximum_scan_distance': 10.0,
            'link_match_minimum_response_fine':  0.45,
            'link_scan_maximum_distance':        2.5,
            'loop_search_maximum_distance':      3.0,
            'do_loop_closing':                   True,
            'loop_match_minimum_chain_size':     10,
            'loop_match_maximum_variance_coarse': 3.0,
            'loop_match_minimum_response_coarse': 0.35,
            'loop_match_minimum_response_fine':   0.45,

            # 位姿求解器
            'solver_plugin':            'solver_plugins::CeresSolver',
            'ceres_linear_solver':      'SPARSE_NORMAL_CHOLESKY',
            'ceres_preconditioner':     'SCHUR_JACOBI',
            'ceres_trust_strategy':     'LEVENBERG_MARQUARDT',
            'ceres_dogleg_type':        'TRADITIONAL_DOGLEG',
            'ceres_loss_function':      'None',

            # 使用 /odometry/filtered 作为里程计输入（来自上面的 EKF 融合）
            'use_pose_extrapolator': True,

            # 启动时不加载已有地图（全新建图）
            'use_map_saver': True,
        }]
    )

    return LaunchDescription([
        rf2o_node,
        ekf_fusion_node,
        slam_toolbox_node,
    ])