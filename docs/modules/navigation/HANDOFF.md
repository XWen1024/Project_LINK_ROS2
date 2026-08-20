# Navigation Two 交接文档

Status: current; hardware aliases and no-motion sensor gates passed; supervised motion pending
Last verified: 2026-08-19
Verified code: `main@3210e70`

本文档是 Project LINK 当前 Point-LIO + slam_toolbox + Navigation2 路线的
长期调试入口。内容从 `AGENTS.md`、`PROGRESS.md` 和已完成的 Orin 实机验证中
提取。后续修改导航架构、TF、代价地图、控制器或启动流程时，应同步更新本文档。

## 1. 当前结论

- 当前路线已经能够完成 Point-LIO 实时定位、2D 在线建图和 Nav2 导航。
- 默认 SSH：`wte@orin`。
- Orin 主仓库：`/home/wte/wheeltec_robot`。
- 外部 Point-LIO 工作区：`/home/wte/point_lio_ws`。
- ROS 网络：`ROS_DOMAIN_ID=42`、`ROS_LOCALHOST_ONLY=0`。
- 小车外廓：长 `0.51 m`、宽 `0.41 m`、高约 `0.82 m`。
- Nav2 有效碰撞外廓含 padding 后约为 `0.53 x 0.43 m`。
- 自动倒车禁用，因为当前没有可靠的后视避障。
- 恢复动作只允许清代价地图、原地旋转和等待。
- Point-LIO 长时间线性积压已经通过轻量化配置和有界队列补丁控制。
- Nav2 定位使用 Point-LIO TF，速度反馈使用 C63A `/odom`。
- 车头 icSpring 摄像头由 Orin 以 `/dev/project_link_front_camera` 独占，
  发布 `/front_camera/image/compressed`；中控仅在地图侧边小框渲染。
  摄像头故障不得阻断建图或 Nav2 的基本启动。2026-08-20 两端原始 JPEG
  校验确认 Orin 与 Ubuntu 收到的 1280x720 帧均完整；严重偏橙来自相机
  自动白平衡卡在错误状态，不是 USB 松动或 DDS 半帧拼接。
- 车头摄像头继续原生采集 `1280x720 MJPEG @ 30 FPS`，静态抓拍保留最新
  原生帧。现场 A/B 验证表明全帧率跨 Wi-Fi 预览约占 `22 Mbps`，会将双向
  RTT 从约 `20 ms` 推高到 `200–300 ms`；网络不佳时应通过 Orin
  `console.env` 的 `FRONT_CAMERA_PREVIEW_FPS` 降至 `10`；当前生产默认使用
  更稳定的 `24 FPS`，`30 FPS` 仅作为高级选项。默认固定曝光为
  `exposure=300`、`gain=48`；相机节点
  启动时会关闭再开启自动白平衡，强制重新收敛。中控高级模式可即时切换
  自动曝光、调节曝光/增益，也可切换自动白平衡或设置 2800–6500 K 手动
  色温。GUI 只调用该节点固定的 ROS 参数服务，不接收
  任意命令；长期默认值仍由全局设置中的 allowlist 配置保存。
- 中控的三维点云开关必须真正创建/销毁 `/unilidar/cloud` DDS 订阅，不能只
  在回调里丢弃数据；关闭点云时不应继续让约 `4.4 Mbps` 点云占用无线链路。
- 中控离开建图导航页时必须销毁地图、全局/局部代价地图、扫描、路径和车头
  摄像头订阅；跌倒页仅在可见时复用车头图像并订阅证据图。Fast DDS 由
  `scripts/project_link_dds_profile.sh` 动态绑定到路由选中的唯一 IPv4 接口，
  禁止同网段双网卡重复发送同一份 DDS 数据。2026-08-20 当前临时路由器会
  丢弃 Orin 有线与希沃 Wi-Fi 之间的双向 UDP，因此 Orin 暂用 allowlist 中的
  `PROJECT_LINK_DDS_INTERFACE=wlP1p1s0`，两端均走 Wi-Fi 单接口；车载 CPE
  通过跨介质 UDP/组播 gate 后再清除此 override，切换 Orin 有线。

## 2. 一键脚本

脚本均位于：

```bash
/home/wte/wheeltec_robot
```

| 脚本 | 功能 |
|---|---|
| `navigation_two_start.sh` | 完整启动底盘、Point-LIO Phase B、在线地图和 Nav2 |
| `navigation_two_start_navigation.sh` | 与 `navigation_two_start.sh` 相同，名称更明确 |
| `navigation_two_start_mapping.sh` | 启动底盘和在线建图，并停止 Nav2，供键盘遥操建图 |
| `navigation_two_save_map.sh` | 保存 occupancy map，并尽力保存 slam_toolbox posegraph |
| `navigation_two_status.sh` | 打开综合状态 tmux，检查 topic、LIO 频率、TF、动作和速度端点 |
| `navigation_two_stop.sh` | 先发零速度，再停止 Navigation Two 全部 tmux 和节点 |
| `navigation_two_start_uwb.sh` | 在健康 Nav2 上启动 UWB shadow/live 桥，不发送任务目标 |
| `navigation_two_uwb.sh` | 查看状态或发送召唤、跟随、停止高层命令 |

最短命令清单见 `docs/modules/navigation/COMMANDS.md`。

## 3. 当前数据流

### 3.1 定位与建图

```text
/unilidar/cloud + /unilidar/imu
-> point_lio
-> /odom_lio_raw + lio_odom -> lio_base
-> lio_planar_projection
-> /odom_lio + odom -> base_footprint

/unilidar/cloud
-> pointcloud_to_laserscan
-> /scan
-> laser_scan_accumulator
-> /scan_accumulated
-> slam_toolbox
-> /map + map -> odom
```

### 3.2 Navigation2

```text
Point-LIO map -> odom -> base_footprint TF
-> Nav2 pose/localization

C63A /odom twist
-> controller_server + bt_navigator velocity feedback

/scan_accumulated
-> local/global obstacle layers

NavFn global planner
-> /plan
-> DWB local controller
-> /local_plan
-> velocity_smoother
-> /cmd_vel
-> C63A base
```

不要同时启动 rf2o/EKF 路线和 Point-LIO 路线。只能有一套节点拥有
`odom -> base_footprint`。

## 4. TF 与 URDF

唯一权威模型：

```text
src/turn_on_wheeltec_robot/urdf/patrol_robot.urdf.xacro
```

权威链：

```text
base_footprint
-> base_link
-> unilidar_link
-> unilidar_lidar
-> unilidar_imu
```

关键几何：

- `base_footprint -> base_link` 只有 `z=0.0785 m`，没有横向偏移。
- URDF 车体视觉模型围绕 `base_footprint` 对称。
- Nav2 footprint 同样围绕 `base_footprint` 对称：

```text
[[-0.255, -0.205],
 [-0.255,  0.205],
 [ 0.255,  0.205],
 [ 0.255, -0.205]]
```

- `footprint_padding=0.01 m`。
- 2026-08-20 室内通道实测后，局部与全局 inflation layer 统一为
  `inflation_radius=0.28 m`、`cost_scaling_factor=5.0`。不得为通过狭窄通道
  缩小真实 footprint；如果仍失败，先区分黑色硬占用栅格、目标超出全局地图
  和粉红软膨胀代价。
- 雷达安装平移保留为：`x=0.190 m`、`y=0.000 m`、`z=0.550 m`，其中真实
  `lidar_offset_y` 尚未进行独立物理测量。
- 2026-08-20 现场静止点云标定后的底盘安装姿态为：roll
  `-1.5707963268 rad`（`-90°`）、pitch `-0.0383972435 rad`（约 `-2.2°`）、
  yaw `1.5707963268 rad`（`90°`）。标定约束为 Unitree 原始前向轴映射到
  底盘 `+X`，实测地面映射到车底 `z≈0`；RViz2 红色生产点云与绿色预览
  点云已验证重合。
- Unitree 驱动帧内部固定修正仍为 roll `pi`、pitch `0`、yaw
  `2.0112063268 rad`（约 `115.234°`）；它不是中控显示的机械安装角，禁止
  把两组角度混为一组。
- Unitree L1 点云和 IMU 轴平行，Point-LIO `extrinsic_R` 保持单位阵。

如果实体车相对 RViz footprint 始终固定横移，而 RViz 中 footprint 和
`/local_plan` 本身居中，应测量 `lidar_offset_y`，或者通过受控原地旋转拟合，
不要直接按一次撞墙的视觉感觉猜半个车宽。

## 5. Point-LIO Phase B 实时配置

正常建图和导航使用 Phase B 轻量模式：

- `odom_only=true`
- `point_filter_num=2`
- `filter_size_surf=0.15`
- `filter_size_map=0.15`
- `cube_side_length=150.0`
- `mapping.det_range=40.0`
- LiDAR 内部缓存上限：2 帧
- IMU 内部缓存上限：2000 条
- ROS 输入 QoS 同样有界
- 点云、Path、Odom 发布队列深度降至合理值

Phase B 不发布 `/point_lio/cloud_registered`。需要看原生 3D 注册点云时，单独
启动 Phase A，不要在正常 Nav2 运行时打开重负载输出。

外部源码补丁：

```text
patches/point_lio/0001-bound-realtime-queues.patch
scripts/apply_point_lio_realtime_patch.sh
```

新装或重建外部 Point-LIO 工作区时：

```bash
cd /home/wte/wheeltec_robot
./scripts/apply_point_lio_realtime_patch.sh --check
./scripts/apply_point_lio_realtime_patch.sh

cd /home/wte/point_lio_ws
source /opt/ros/humble/setup.bash
colcon build --symlink-install --packages-select point_lio
```

补丁脚本幂等，不会 reset 外部脏工作区的其他文件。

已验证短时状态：

- `/unilidar/cloud`：约 `9.6-9.8 Hz`
- `/odom_lio`：约 `9.6 Hz`
- `/scan_accumulated`：约 `9.6 Hz`
- `/odom_lio` 时间戳延迟：约 `0.03 s`
- LiDAR 队列：通常 `1/2`
- IMU 队列：通常约 `25/2000`
- 内部 backlog：正常为 `0.000 s`

旧故障表现是输入约 `9.6 Hz`、输出约 `8.5 Hz`，队列每秒积压约 1 帧，最终
形成 20 秒以上延迟。不要用增大 TF tolerance 或伪造时间戳掩盖该问题。

## 6. LaserScan 与 slam_toolbox

Unitree L1 单帧 2D 切片很碎，原始 `/scan` 单帧有效角度覆盖曾只有约 18%。
`laser_scan_accumulator` 使用 Point-LIO 的时间戳 TF 做运动补偿：

- 固定坐标：`odom`
- 输出坐标：`base_link`
- 累积窗口：`3.0 s`
- voxel：`0.04 m`
- 最低有效覆盖：25%
- 输出：`/scan_accumulated`

正常状态约保留 29-30 帧，覆盖率约 40%-46%。slam_toolbox 必须订阅
`/scan_accumulated`，原始 `/scan` 只用于 RViz 和诊断。

## 7. Nav2 当前参数

配置文件：

```text
src/wheeltec_robot_nav2/param/wheeltec_params/param_point_lio_navigation.yaml
```

主要设置：

- 全局规划器：NavFn，A* 开启，`allow_unknown=false`
- 局部控制器：DWB
- 最大线速度：`0.18 m/s`
- 最大角速度：控制器 `0.50 rad/s`，平滑器 `0.60 rad/s`
- 禁止倒车：`min_vel_x=0.0`，平滑器最小线速度为 0
- 局部窗口：`3 x 3 m`，分辨率 `0.05 m`
- local/global inflation：半径 `0.40 m`，cost scaling `3.5`
- 目标完成：位置 `0.25 m`，航向 `0.50 rad`
- progress checker：20 秒内至少移动 `0.10 m`
- 恢复：清图、一次完整原地扫描、等待；无 BackUp

### 7.1 速度反馈修复

Nav2 pose 和速度故意使用不同来源：

- pose：Point-LIO TF
- twist：C63A `/odom`

不要把 controller 或 bt_navigator 的 `odom_topic` 改回 `/odom_lio`。Point-LIO
状态中的线速度位于世界坐标系，旧投影节点直接将其复制进 child frame 为
`base_footprint` 的 Odometry twist。实机静止时曾出现：

```text
/odom_lio linear.x =  0.020 m/s
/odom_lio linear.y = -0.037 m/s
/odom     linear.x =  0.000 m/s
/odom     linear.y =  0.000 m/s
```

这会让 DWB 误以为差速底盘持续横向滑动，造成局部轨迹偏离、无意义转向和
`Failed to make progress`。当前 controller_server 和 bt_navigator 已实际订阅
`/odom`，`min_y_velocity_threshold=0.5` 用于钳制非完整约束底盘的横向噪声。

## 8. RViz 推荐显示

Fixed Frame：

```text
map
```

至少显示：

- Map `/map`
- LaserScan `/scan_accumulated`，Decay Time 可设 3 秒用于观察
- Path `/plan`：全局规划路线，建议绿色
- Path `/local_plan`：DWB 实际跟踪轨迹，建议红色或蓝色
- Polygon `/local_costmap/published_footprint`
- Map `/local_costmap/costmap`
- Map `/global_costmap/costmap`
- TF
- RobotModel

绿色 `/plan` 是给 `base_footprint` 参考点的全局路线，不是车体某一侧边缘。
判断撞墙问题时必须同时看 `/local_plan` 和 published footprint。

## 9. 常见问题排查

### 9.1 局部代价地图全白

依次检查：

```bash
ros2 topic hz /scan_accumulated
ros2 topic delay /odom_lio
ros2 run tf2_ros tf2_echo odom base_footprint
```

如果 `/odom_lio` 延迟持续增大，先保存地图，再重启 Phase B。不要先改分辨率。

另一个已修复原因是 LaserScan 高度过滤。当前 obstacle layer 接受
`-0.1..2.0 m`，不要恢复为只接受地面附近的默认范围。

### 9.2 绿色 Path 正确但实体车不沿中心走

1. 看 `/local_plan` 是否也偏离 `/plan`。
2. 看 RViz footprint/RobotModel 是否沿 `/local_plan` 居中。
3. 确认 controller_server 和 bt_navigator 订阅 `/odom`，不是 `/odom_lio`。
4. 如果 RViz 模型居中而实体车固定偏移，再校准 `lidar_offset_y` 或底盘真实
   旋转中心。
5. 如果 `/local_plan` 自己偏离，则继续调 DWB critic，不要改 URDF。

### 9.3 明明到终点却不结束

当前容差已放宽为 `0.25 m / 0.50 rad`。若 Path 仍不消失，检查：

- 当前目标是否要求不合理的最终朝向
- `/odom` 是否仍报告运动
- 是否触发 progress checker 或恢复动作

### 9.4 到桌边后转圈、卡住或倒车

- 倒车已经禁止；若再次出现，检查是否运行了其他旧 Nav2 配置。
- 当前恢复允许一次受碰撞检查的 `6.28 rad` 原地扫描。
- 检查 `/cmd_vel` 发布者，键盘遥操和 Nav2 不得同时控制底盘。

### 9.5 贴墙、蹭墙角

- 确认 local/global footprint 完全一致。
- 查看墙是否在 local costmap 中被标为 lethal/inflated，而不只是在 `/map` 中。
- 查看 `/local_plan`，不要只看 `/plan`。
- 不要盲目增大 footprint；先判断是 TF 参考点、真实外壳尺寸还是 DWB 偏轨。

## 10. tmux 会话

常用会话：

```text
project_link_c63_base
project_link_point_lio
project_link_point_lio_nav2
project_link_navigation_two_save
project_link_navigation_two_status
```

已有基础脚本仍可单独使用：

```bash
./start_point_lio_tmux.sh --restart --with-2d-map
./start_point_lio_nav2_tmux.sh --restart
./scripts/c63_keyboard_teleop.sh
```

## 11. 保存内容

`navigation_two_save_map.sh` 默认保存到：

```text
/home/wte/maps/navigation_two_YYYYMMDD_HHMMSS.yaml
/home/wte/maps/navigation_two_YYYYMMDD_HHMMSS.pgm
/home/wte/maps/navigation_two_YYYYMMDD_HHMMSS.posegraph
/home/wte/maps/navigation_two_YYYYMMDD_HHMMSS.data
```

occupancy map 用于 Nav2/map_server；posegraph 用于 slam_toolbox 后续继续建图。
posegraph 保存失败不会阻止 occupancy map 保存，具体输出在
`project_link_navigation_two_save` tmux 中。

2026-08-04 实机验证：完整启动入口能够识别并复用现有健康栈；状态 tmux
持续刷新并确认六个关键 topic；保存入口成功生成 `.yaml`、`.pgm`、
`.posegraph` 和 `.data`。

## 12. 安全边界

- 一键启动脚本不发送导航目标和非零速度。
- 使用 RViz `2D Goal Pose` 前确认键盘遥操已关闭。
- 使用键盘遥操建图前使用 mapping 脚本停掉 Nav2。
- 实车运动时保持急停或断电手段可用。
- 当前后方没有可靠避障，不允许自动倒车。
- 雷达停止转动、点云时间戳飞掉或定位跳变时立即停止目标并重启 Phase B。
- UWB 召唤/跟随只允许调用 `/navigate_to_pose`，不得新增 `/cmd_vel` 发布者。
- UWB shadow 是默认模式；live 需要有效实测标定和本地 `UWB-NAV2` 确认。
- UWB 过期、TF 过期、串口断开、Nav2 失败或额外速度发布者出现时取消目标。

## 12.1 UWB 可选叠加层

UWB 不属于 Navigation Two 基础启动，必须在本栈健康后单独启动：

```bash
./navigation_two_start_uwb.sh --shadow \
  --device /dev/uwb-bu04 \
  --params ~/.config/project_link/uwb_navigation.yaml
```

数据流、协议、标定和现场门禁见 `docs/modules/uwb/HANDOFF.md`。
`navigation_two_stop.sh` 会先请求 `/uwb_navigation/stop`，再关闭 UWB、Nav2、
Point-LIO 和底盘会话。

## 13. 后续 TODO

- Phase A 连续运行 30 分钟。
- Phase B 连续运行 30 分钟。
- Phase B + Nav2 连续运行 30 分钟。
- 通过标准：LIO lag `<0.2 s` 且不单调增长、LiDAR 队列不超过 2、
  `/odom_lio` 维持约 9.5 Hz。
- 如仍存在固定物理横移，测量或拟合 `lidar_offset_y`。
- 在安全空旷区域继续比较 `/plan`、`/local_plan` 和实体车中心轨迹。

## 14. 关键文件

```text
start_point_lio_tmux.sh
start_point_lio_nav2_tmux.sh
navigation_two_start_mapping.sh
navigation_two_start_navigation.sh
navigation_two_save_map.sh
navigation_two_status.sh
navigation_two_stop.sh
configs/point_lio/unilidar_l1_project_link.yaml
configs/point_lio/lio_planar_projection.yaml
src/turn_on_wheeltec_robot/urdf/patrol_robot.urdf.xacro
src/turn_on_wheeltec_robot/src/lio_planar_projection.cpp
src/wheeltec_robot_nav2/param/wheeltec_params/param_point_lio_navigation.yaml
src/wheeltec_robot_nav2/behavior_trees/point_lio_safe_replanning.xml
patches/point_lio/0001-bound-realtime-queues.patch
```
