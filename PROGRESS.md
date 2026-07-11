# Project LINK / 灵犀 助老移动操作机器人 Progress 进度文档

## Current Status - 2026-07-11

### Point-LIO Phase A coordinate architecture implemented

* Point-LIO no longer publishes its raw 6D pose directly as
  `odom -> base_footprint`.
* The new Phase A chain is:

```text
/unilidar/cloud + /unilidar/imu
-> Point-LIO /odom_lio_raw and lio_odom -> lio_base
-> lio_planar_projection
-> /odom_lio and odom -> base_footprint
```

* `lio_planar_projection` preserves the original 3D LIO output, applies the
  versioned LIO-to-base calibration seed from the installed URDF, and publishes
  a base pose constrained to `z=0`, `roll=0`, and `pitch=0`.
* Point-LIO's `publish_odometry_without_downsample` is now disabled so raw odom
  is emitted at lidar-frame cadence instead of the previous unnecessary high
  loop rate.
* Phase A acceptance is now: real cloud, IMU, raw odom, planar odom, registered
  cloud, unique TF, 60-second stationary check, and low-speed straight/turn
  calibration. Do not start `--with-2d-map` before those checks pass.
* Initial Orin bringup passed on commit `f647484`:
  * `/unilidar/cloud`: about `9.8 Hz`
  * `/unilidar/imu`: about `249 Hz`
  * `/odom_lio_raw`, `/odom_lio`, `/point_lio/cloud_registered`: about `9.7-9.9 Hz`
  * raw odom has one `point_lio_mapping` publisher; planar odom has one
    `lio_planar_projection` publisher.
  * raw TF retains the expected 3D LIO attitude; projected
    `odom -> base_footprint` is verified at `z=0`, roll `0`, pitch `0`.
* The remaining Phase A calibration item is chassis heading: the initial
  projected yaw is about `-65 degrees` while stationary. Perform the low-speed
  straight-line test, then tune only `odom_to_lio_odom_yaw` and, if needed, the
  versioned LIO-to-base offsets in `configs/point_lio/lio_planar_projection.yaml`.

### Direct RViz A-to-B loop requested

* User clarified the next desired minimum closed loop is not Nav2:
  * keep the current SLAM/TF pose source running,
  * view the map in RViz,
  * click A and B in RViz,
  * directly publish `/cmd_vel` to move from A toward B.
* No path planning, no obstacle avoidance, no costmaps, and no Nav2 lifecycle
  stack are desired for this test.
* Added `scripts/rviz_ab_drive.py`:
  * subscribes to `/clicked_point`,
  * treats first click as A/start sanity check,
  * treats second click as B/target,
  * uses TF `map -> base_footprint`,
  * publishes low-speed differential `/cmd_vel` only with `--enable-motion`.

### C63A base integrated into current rf2o SLAM bringup

* `start_slam_tmux.sh --restart` now treats the C63A base as part of the normal
  known-good SLAM route.
* New default tmux layout includes a `base` window running:
  `ros2 launch turn_on_wheeltec_robot base_serial.launch.py`
* The `slam` window waits for real `/odom` and `/scan` messages before launching
  `rf2o_slam_toolbox.launch.py`.
* The monitor now checks `/odom`, `/imu/data_raw`, `/PowerVoltage`, `/scan`,
  `/odom_rf2o`, `/odometry/filtered`, `/map`, `odom -> base_footprint`, and
  `map -> odom`.
* `--no-base` remains available for lidar-only debugging.
* Intended current data flow:

```text
/odom from C63A base + /scan from Unitree lidar
-> rf2o_laser_odometry
-> /odom_rf2o
-> robot_localization EKF
-> /odometry/filtered and odom -> base_footprint TF
-> slam_toolbox
-> /map and map -> odom TF
```

Point-LIO remains an evaluation route. Its planar projection adapter is now in
place, but it must complete the Phase A physical validation before it replaces
the known-good rf2o chain for mapping or direct motion.

### C63A base handoff update

* C63A ROS serial link was verified on Orin:
  * USB: `1a86:55d4`
  * alias: `/dev/wheeltec_controller -> /dev/ttyACM0`
  * baud: `115200`
* After power cycling the C63A board, return data was confirmed:
  * `/odom`: about `20 Hz`
  * `/imu/data_raw`: about `20 Hz`
  * `/PowerVoltage`: about `26.27 V` during the check
* A small `/cmd_vel` test produced odom movement, confirming Orin -> C63A command
  path and C63A -> Orin return path.
* Added differential keyboard teleop helpers:
  * `scripts/c63_keyboard_teleop.sh`
  * `scripts/ssh_c63_keyboard_teleop.ps1`
* Added handoff doc for the next integrator:
  * `docs/C63A_BASE_AND_SLAM_HANDOFF.md`
* Continue to keep Nav2 paused. Next real integration goal is base odom + lidar +
  Point-LIO/SLAM stability, not autonomous navigation.

## Current Status - 2026-06-27

本小节记录仓库迁移和当前调试策略的最新状态；下方原始交接内容继续保留，作为项目长期背景和路线图。

### 仓库与 Orin 状态

* GitHub 私有仓库、本地 Windows 仓库、Orin 仓库已完成同步。
* Orin 当前工作区：`/home/wte/wheeltec_robot`
* Orin 旧工作区备份：`/home/wte/wheeltec_robot_backup_20260627_1250`
* Git remote：`git@github.com:XWen1024/Project_LINK_ROS2.git`
* 当前主分支：`main`
* 已迁入并构建通过的 ROS 2 包：
  * `turn_on_wheeltec_robot`
  * `wheeltec_nav2`
  * `wheeltec_slam_toolbox`
  * `rf2o_laser_odometry`
  * `serial`
  * `wheeltec_robot_msg`

### 已验证事项

* `colcon build --symlink-install` 已在 Orin 上一次通过，6 个包构建成功。
* `ros2 pkg list` 能看到上述迁移包。
* `ros2 pkg executables` 已确认：
  * `turn_on_wheeltec_robot ImuProcessor`
  * `turn_on_wheeltec_robot wheeltec_robot_node`
  * `rf2o_laser_odometry rf2o_laser_odometry_node`
* `wheeltec_nav2` 的 `patrol_nav2.launch.py` 已修复：
  * ROS 包名应为 `wheeltec_nav2`
  * 不是源码目录名 `wheeltec_robot_nav2`
* `patrol_nav2.launch.py --show-args` 已能正常展开，但 Nav2 当前不是下一步目标。
* 上一版 `rf2o + EKF + slam_toolbox` 已在 RViz2 正常出图，可作为 fallback。
* Point-LIO 外部工作区 `/home/wte/point_lio_ws` 已在 Orin 构建通过；主仓库已新增项目配置、wrapper launch 和 tmux 启动脚本。
* 2026-06-27 追加更新规范：仓库文件以 GitHub 为同步源。本地修改后先提交并 push，再在 Orin 用 `git pull --ff-only` 更新；不再用 `scp` 等方式直接替换 Orin 仓库文件。涉及 launch、配置、硬件脚本或流程文档的变更，可以提高提交频率，方便回滚。

### 当前阶段策略

现阶段固定为 **SLAM-first**：

```text
已跑通的 fallback：雷达 /scan
-> TF 树
-> rf2o 激光里程计
-> EKF /odometry/filtered
-> slam_toolbox /map
当前验证：/unilidar/cloud + /unilidar/imu
-> Point-LIO /odom_lio
-> slam_toolbox /map
-> 再回到 Nav2
```

当前不直接跑 Nav2。Nav2 要等 SLAM、odom、TF 稳定，并且更合适的 SLAM 方案验证后再继续。

### 当前 SLAM 验证入口

当前候选链路：

```bash
ros2 launch turn_on_wheeltec_robot rf2o_slam_toolbox.launch.py
```

2026-06-27 追加：根目录新增一键 tmux 启动脚本，作为当前 Orin 侧推荐入口：

```bash
cd /home/wte/wheeltec_robot
./start_slam_tmux.sh --restart
```

该脚本创建 `project_link_slam` tmux session，并并行启动 Unitree 雷达驱动、`unilidar_p2s.launch.py`、`robot_mode_description.launch.py`、`rf2o_slam_toolbox.launch.py` 和一个 topic/TF 监控窗口。它不启动 Nav2，也不发布 `/cmd_vel`。

2026-06-27 追加：上一版 `rf2o + EKF + slam_toolbox` 已能在 RViz2 正常出图；下一阶段开始接入 Point-LIO 作为 3D LiDAR odometry。Point-LIO 源码先放在 Orin 外部实验工作区 `/home/wte/point_lio_ws`，本仓库只维护项目配置、wrapper launch、tmux 启动脚本和文档。

Point-LIO 阶段 A 入口：

```bash
cd /home/wte/wheeltec_robot
./start_point_lio_tmux.sh --restart
```

Point-LIO 阶段 B 入口：

```bash
cd /home/wte/wheeltec_robot
./start_point_lio_tmux.sh --restart --with-2d-map
```

该路线固定输入 `/unilidar/cloud` 与 `/unilidar/imu`，输出统一为 `/odom_lio` 和 `odom -> base_footprint`。不要与旧 `rf2o_slam_toolbox.launch.py` 同时运行，避免重复发布 odom TF。

2026-06-28 追加：Point-LIO Phase A 现在由 `point_lio_unilidar_l1.launch.py` 自己补发 `unilidar_link -> unilidar_lidar` 静态 TF；Phase B 使用 `--with-2d-map` 时由 `unilidar_p2s.launch.py` 发布该 TF，Point-LIO wrapper 会关闭重复 TF。

2026-06-28 追加：旧 `rf2o + EKF + slam_toolbox` 脚本复测可正常出图，确认雷达硬件、Unitree driver、`/unilidar/cloud`、`/unilidar/imu`、`/scan` 链路可用。Point-LIO 一键脚本已改为等待真实 cloud/IMU 消息后再启动 Point-LIO，并暴露 `LIDAR_TF_ROLL/PITCH/YAW` 用于调整点云方向。注意：`./start_point_lio_tmux.sh --restart` 是 Phase A，只验证 `/odom_lio` 和注册点云；要出 2D `/map` 必须使用 `--with-2d-map`。

意图数据流：

```text
/scan
-> rf2o_laser_odometry
-> /odom_rf2o
-> robot_localization EKF
-> /odometry/filtered and odom -> base_footprint TF
-> slam_toolbox
-> /map and map -> odom TF
```

硬件验证顺序：

1. 只接雷达，确认 `/scan` 和 RViz2 可视化。
2. 启动 `robot_mode_description.launch.py`，确认 `base_footprint -> base_link -> unilidar_link -> unilidar_lidar`。
3. 启动当前 `rf2o_slam_toolbox.launch.py`，检查 `/odom_rf2o`、`/odometry/filtered`、`map -> odom`。
4. 如需验证轮式 `/odom`，再接 STM32 主板；不启动 Nav2，不发 `/cmd_vel`。
5. Point-LIO 阶段 A 先验证 `/odom_lio` 和注册点云；阶段 B 再接回 `slam_toolbox` 生成 `/map`。

### 局域网 RViz2 可视化约定

Orin 和 Ubuntu 可视化电脑使用相同 ROS 2 网络环境：

```bash
export ROS_DOMAIN_ID=42
export ROS_LOCALHOST_ONLY=0
```

Orin 推荐使用仓库脚本，不默认污染 `~/.bashrc`：

```bash
source /home/wte/wheeltec_robot/scripts/project_link_env.sh
```

Ubuntu 可视化电脑：

```bash
export ROS_DOMAIN_ID=42
export ROS_LOCALHOST_ONLY=0
source /opt/ros/humble/setup.bash
rviz2
```

RViz2 初始显示项：

* `TF`
* `LaserScan`：`/scan`
* `Map`：`/map`
* `Odometry`：`/odom_rf2o` 或 `/odometry/filtered`

### 安全默认

* 当前阶段不要启动 Nav2。
* 不主动发布 `/cmd_vel`。
* 硬件测试优先只接雷达；需要底盘时先架空轮子、断开动力或准备急停。
* 不要同时启动多个会发布同一条 `odom -> base_footprint` 的 EKF / odom TF 源。

---

## 0. 文档目的

本文档用于记录当前机器人项目的整体情况和实时进度，包括项目定位、硬件架构、软件架构、当前进展、关键问题、下一步优先级和建议实施路线。

项目当前处于“多子系统分别跑通，正在向端到端闭环集成”的阶段。现阶段最重要目标不是继续堆新功能，而是跑通第一条完整 MVP 链路：

```text
语音指令
→ 结构化任务解析
→ Nav2 导航到目标地点
→ 视觉识别目标物体
→ SO-101 机械臂抓取
→ 放入托盘
→ 返回用户位置
→ 语音反馈
```

---

## 1. 项目基本定位

项目名称：

```text
Project LINK / 灵犀
```

项目方向：

```text
基于 ROS 2 与端云协同架构的具身智能助老移动操作机器人
```

核心目标：

* 面向居家老年用户
* 支持自然语言交互
* 支持室内 SLAM 与导航
* 支持桌面物体识别与抓取
* 支持递药、取物等轻量家务辅助
* 后续支持跌倒检测与应急响应
* 后续支持远程自然语言控车和 Agent 接入

项目不是单纯的 ROS2 小车，也不是单纯机械臂 Demo，而是一台正在从 AGV 底盘升级为“移动操作机器人”的助老服务原型。

---

## 2. 总体系统设计

系统采用“端侧小脑 + 云端大脑”的架构。

### 2.1 云端大脑

云端主要负责高层语义理解，包括：

* ASR 后的自然语言理解
* 用户意图解析
* 任务结构化
* 输出 JSON 指令
* 必要时调用大模型/VLM做复杂判断

云端不直接控制电机、底盘或机械臂。

### 2.2 端侧小脑

端侧主要负责实时性和安全性要求较高的任务，包括：

* ROS 2 节点运行
* 底盘控制
* SLAM / 里程计 / 建图
* Nav2 导航
* 视觉检测
* 机械臂控制
* 视觉伺服
* 末端测距
* 急停、安全状态机
* 本地失败恢复

### 2.3 任务执行基本流程

以“帮我把桌上的药拿过来”为例：

```text
1. 用户唤醒机器人
2. 用户说出自然语言指令
3. ASR 转文本
4. LLM 解析意图
5. 输出结构化 JSON
6. Python 中控节点接收任务
7. 查询地点数据库
8. Nav2 导航到目标地点附近的操作位
9. 视觉系统搜索目标物体
10. 机械臂移动到预抓取区域
11. 手眼相机进行近距离视觉伺服
12. 末端 ToF / 激光测距判断距离
13. 夹爪闭合抓取
14. 抬起并放入车上托盘
15. Nav2 返回用户位置
16. TTS 播报任务结果
```

---

## 3. 目标 MVP

当前最重要的 MVP 建议固定为：

```text
用户语音说“把客厅桌上的药瓶拿过来”
→ 机器人解析任务
→ 导航到客厅桌前操作位
→ 识别桌上的药瓶
→ 使用 SO-101 抓起药瓶
→ 放入机器人托盘
→ 返回用户身边
→ 播报完成
```

该 MVP 覆盖项目最核心能力：

* 语音交互
* LLM 结构化输出
* ROS2 任务编排
* Nav2 点到点导航
* 目标识别
* 机械臂抓取
* 任务闭环反馈

暂时不建议在 MVP 前加入过多复杂家务，例如开冰箱、拉抽屉、擦桌子、收衣服等。

---

## 4. 硬件架构

### 4.1 供电系统

当前设计偏工程化，采用 24V 动力电源系统。

主要组成：

```text
24V 锂电池
急停开关
50A 分流器
电压电流表
+24V 正极总线
GND 负极总线
12V 10A 降压模块
12V 5A 降压模块
5V 10A 降压模块
```

供电分配理解：

```text
24V：底盘电机、动力系统
12V：部分传感器、机械臂驱动板、外设
5V：Jetson Orin Nano、USB 设备、低压逻辑设备
```

注意事项：

* 急停必须位于动力系统关键路径上
* Jetson 与底盘电机供电需要考虑噪声隔离
* 大电流线缆与 USB / 相机线缆应尽量分开走线
* 所有 GND 需要统一参考，但要避免不合理的大电流回流路径
* 机械臂和底盘同时动作时需要检查电压跌落

---

### 4.2 计算平台

主计算设备：

```text
Jetson Orin Nano
```

主要职责：

* 运行 ROS 2 主系统
* 运行感知节点
* 运行视觉检测
* 运行任务中控
* 接收语音服务任务指令
* 与 STM32 底盘控制器通信
* 与机械臂驱动通信
* 管理 USB 摄像头、深度相机、雷达等设备

辅助设备：

```text
USB 3.0 Hub
5G CPE
```

连接关系：

```text
Jetson Orin Nano
├── USB Hub
│   ├── Unitree L1
│   ├── 手眼相机
│   ├── 广角相机
│   ├── 深度相机
│   ├── 麦克风
│   └── 机械臂/其他 USB 设备
├── USB 串口连接 STM32
└── WiFi / 以太网连接 5G CPE
```

---

### 4.3 底盘系统

底盘类型：

```text
差速轮式 AGV 底盘
```

主要组成：

```text
STM32 / C63A 主控
ZLAC8015D 电机驱动器
左右轮毂电机
CAN 总线
轮式里程计
```

控制链路：

```text
Jetson Orin Nano
→ USB 串口
→ STM32
→ CAN
→ ZLAC8015D
→ 左右轮毂电机
```

底盘当前关键任务：

* 稳定发布轮式里程计
* 稳定接收 `/cmd_vel`
* 与 ROS2 控制链路打通
* 为 SLAM 和 Nav2 提供可靠运动基础
* 处理急停、限速、通信中断保护

---

### 4.4 感知系统

计划/已有感知设备：

```text
Unitree L1 激光雷达
广角相机
手眼相机
深度相机
麦克风
末端 ToF / 激光测距模块
```

各传感器职责建议如下：

| 传感器           | 主要职责                  |
| ------------- | --------------------- |
| Unitree L1    | SLAM、建图、里程计、导航感知      |
| 广角相机          | 场景级观察、目标粗定位、跌倒检测视觉确认  |
| 手眼相机          | 机械臂近距离目标对准、视觉伺服       |
| 深度相机          | 目标 3D 粗定位、桌面高度估计、辅助抓取 |
| 麦克风           | 唤醒、ASR、呼救检测、声源定位      |
| 末端 ToF / 激光测距 | 最后几厘米距离判断、夹爪闭合触发      |

---

### 4.5 机械臂系统

机械臂型号：

```text
LeRobot SO-101
```

主要职责：

* 轻量桌面物体抓取
* 药瓶、杯子、小盒子、纸巾包等轻物体操作
* 后续可扩展到抽屉、托盘、按钮等任务

当前抓取方案理解：

```text
YOLO / 开词表检测
→ 手眼相机获取目标位置
→ 视觉伺服调整末端
→ 末端测距判断距离
→ 夹爪闭合
→ 抬起
→ 放入托盘
```

SO-101 能力边界：

* 负载有限
* 刚性有限
* 重复定位精度有限
* 不适合重物
* 不适合高动态动作
* 手腕上不宜挂过重传感器
* 线缆管理非常重要

---

## 5. 软件架构

### 5.1 ROS2 总体结构

建议的 ROS2 模块划分：

```text
voice_service_node
task_orchestrator_node
navigation_manager_node
perception_node
grasp_manager_node
so101_driver_node
tof_range_node
base_driver_node
safety_manager_node
tts_feedback_node
```

推荐的核心通信方式：

```text
/task_command          结构化任务输入
/cmd_vel               底盘速度控制
/odom                  轮式或融合里程计
/tf                    坐标变换
/tf_static             静态坐标变换
/joint_states          机械臂关节状态
/front_camera/image    广角相机图像
/wrist_camera/image    手眼相机图像
/tof/distance          末端测距
/grasp_object          抓取 Action
/navigate_to_pose      Nav2 Action
/task_status           任务状态反馈
/tts_request           语音播报请求
/emergency_stop        急停状态
```

---

### 5.2 语音服务

当前语音链路：

```text
火山引擎 ASR
硅基流动 LLM
火山引擎 TTS
```

当前状态：

```text
ASR → LLM → TTS 链路已跑通
目前更多是独立服务或模拟桩测试
尚未完全 ROS2 节点化
```

下一步：

```text
封装为 ROS2 节点
向 /task_command 发布结构化 JSON
接收 /task_status 后进行 TTS 播报
```

结构化任务示例：

```json
{
  "intent": "fetch_object",
  "target": "medicine_bottle",
  "source_location": "living_room_table",
  "target_location": "user",
  "urgency": "normal"
}
```

语音服务原则：

* LLM 只输出高层任务
* 不允许 LLM 直接输出底盘速度
* 不允许 LLM 直接输出机械臂关节角
* 所有动作必须经过本地状态机、安全检查和 ROS2 Action
* 任务失败时需要返回明确原因

---

### 5.3 任务中控节点

任务中控节点是机器人行为的核心调度器。

建议状态机：

```text
IDLE
LISTENING
PARSING
TASK_RECEIVED
NAVIGATING_TO_SOURCE
SEARCHING_OBJECT
MOVING_TO_PREGRASP
GRASPING
VERIFYING_GRASP
PLACING_TO_TRAY
RETURNING_TO_USER
REPORTING_SUCCESS
REPORTING_FAILURE
RECOVERY
EMERGENCY_STOPPED
```

中控节点职责：

* 接收 `/task_command`
* 校验 JSON 是否完整
* 查询地点数据库
* 调用 Nav2 Action
* 调用抓取 Action
* 处理超时
* 处理失败重试
* 触发 TTS 播报
* 记录任务日志

地点数据库建议格式：

```json
{
  "living_room_table": {
    "display_name": "客厅桌子",
    "nav_pose": {
      "x": 1.82,
      "y": 0.74,
      "yaw": 1.57
    },
    "manipulation_height": 0.75,
    "preferred_arm": "right",
    "search_camera": "front_camera",
    "grasp_camera": "wrist_camera"
  },
  "user_default_position": {
    "display_name": "用户身边",
    "nav_pose": {
      "x": 0.35,
      "y": 0.20,
      "yaw": 3.14
    }
  }
}
```

注意：Nav2 不应导航到“物体本身”，而是导航到“适合机械臂操作的预设操作位”。

---

### 5.4 SLAM 与导航

当前路线：

```text
Unitree L1
→ Point-LIO
→ slam_toolbox
→ Nav2
```

当前已知状态：

```text
使用 STM32 轮式里程计 + L1 点云切片 2D scan 可以跑 slam_toolbox
但回环失败
```

已知问题：

```text
L1 振动通过铝型材传导
L1 内置 IMU 数据不稳定或不可用
2D scan 切片特征不足
轮式里程计精度有限
回环检测不稳定
```

下一步重点：

```text
1. 处理 L1 减振安装
2. 检查 L1 IMU 数据质量
3. 引入 Point-LIO
4. 输出更高质量 odom
5. 再接 slam_toolbox
6. 再接 Nav2
```

建议调试顺序：

```text
1. 静态录包，检查 L1 IMU 噪声
2. 原地旋转录包，检查点云畸变和 odom
3. 低速直线录包，检查轮式里程计比例
4. 跑 Point-LIO，检查 odom 连续性
5. 跑 slam_toolbox，检查地图闭合
6. 接 Nav2，先做短距离点到点导航
7. 最后接入完整任务中控
```

---

### 5.5 视觉识别与抓取

当前问题核心：

```text
识别到目标 ≠ 能稳定抓起目标
```

YOLO 或开词表检测可以回答：

```text
图像里有没有目标
目标大概在哪里
```

但不能直接回答：

```text
目标离夹爪多远
应该从哪里夹
夹爪张多大
何时闭合
失败后怎么恢复
```

推荐抓取分层：

```text
广角相机：目标粗定位
手眼相机：近距离对准
ToF / 激光测距：最后距离判断
SO-101：执行抓取
状态机：处理失败和恢复
```

建议抓取 Action：

```text
GraspObject.action
```

Goal：

```text
string object_name
string source_location
geometry_msgs/PoseStamped rough_pose
```

Feedback：

```text
string stage
float32 confidence
float32 distance
```

Result：

```text
bool success
string reason
```

抓取状态机建议：

```text
DETECT_GLOBAL
MOVE_TO_PREGRASP
DETECT_WRIST
CENTERING
APPROACHING
DISTANCE_CHECK
CLOSING_GRIPPER
LIFTING
VERIFYING
PLACE_TO_TRAY
DONE
FAILED
```

---

## 6. 坐标系与 TF 规划

项目后续要稳定抓取，必须建立清晰 TF 树。

建议 TF 树：

```text
map
└── odom
    └── base_link
        ├── laser_link
        ├── front_camera_link
        │   └── front_camera_optical_frame
        ├── depth_camera_link
        │   └── depth_camera_optical_frame
        └── arm_base_link
            └── arm_link_1
                └── arm_link_2
                    └── arm_link_3
                        └── arm_link_4
                            └── arm_link_5
                                └── ee_link
                                    ├── wrist_camera_link
                                    │   └── wrist_camera_optical_frame
                                    └── tof_link
```

必须标定的外参：

```text
base_link → laser_link
base_link → front_camera_link
base_link → depth_camera_link
base_link → arm_base_link
ee_link → wrist_camera_link
ee_link → tof_link
```

建议标定方法：

| 外参                            | 建议方法                |
| ----------------------------- | ------------------- |
| base_link → laser_link        | 机械测量 + 建图验证         |
| base_link → front_camera_link | 机械测量，后续 AprilTag 微调 |
| base_link → depth_camera_link | 机械测量 + RGBD 标定板     |
| base_link → arm_base_link     | 机械测量，必要时标定修正        |
| ee_link → wrist_camera_link   | 手眼标定                |
| ee_link → tof_link            | 机械测量即可，后续实测修正       |

早期可以先用机械测量给出静态 TF，跑通系统后再逐步精标定。

---

## 7. 推荐抓取硬件方案

### 7.1 当前最推荐方案

```text
前视广角 RGB
手眼 RGB
末端 ToF / 激光测距
```

优点：

* 改动小
* 成本低
* 硬件轻
* 适合 SO-101
* 能解决手眼相机视野小的问题
* 能解决最后距离判断的问题

职责分工：

```text
前视广角 RGB：看到桌面和目标
手眼 RGB：最后对准
末端 ToF：判断离目标还有多远
```

---

### 7.2 中期升级方案

```text
前视 RGB-D
手眼 RGB
末端 ToF / 激光测距
```

优点：

* 前视 RGB-D 能提供目标 3D 粗定位
* 手眼 RGB 继续负责近距离精对准
* ToF 负责最后几厘米闭合触发
* 不把重型 RGB-D 相机挂在机械臂末端

适合阶段：

```text
Nav2 已经稳定
机械臂预抓取位姿需要更准确
希望接入 MoveIt2 或更明确的 3D 抓取
```

---

### 7.3 不建议作为主路线的方案

```text
手眼 RGB-D 作为唯一主感知
```

不优先推荐原因：

* 相机重量可能影响 SO-101
* 线缆拖拽影响机械臂动作
* 近距离深度可能有盲区
* 夹爪容易遮挡深度图
* 手腕运动会造成深度抖动
* 仍然需要手眼标定
* 不解决远距离找目标的问题

可作为实验方案，但不建议押宝。

---

### 7.4 早期强烈建议加入的辅助方案

```text
AprilTag / ArUco 辅助抓取
```

用途：

* 调通坐标系
* 调通机械臂预抓取
* 调通 Nav2 到抓取位
* 调通完整闭环

限制：

* 不适合真实最终场景
* 不能每个物体都贴标签

但早期非常有价值。建议先用 tag 把机器人“导航到桌前并抓起目标”的全链路跑通，再逐步替换成自然物体识别。

---

## 8. 数据采集与后续学习策略

当前阶段不建议直接让学习模型控制全流程。

推荐路线：

```text
规则控制 / 视觉伺服 / ToF
→ 采集成功抓取数据
→ 训练局部抓取策略
→ 用学习策略替代部分手写规则
```

### 8.1 一次 episode 的定义

一次 episode 可以定义为：

```text
机器人已经在桌前
机械臂处于初始位
桌上有目标物体
人类遥操作或半自动控制机械臂抓起物体
放入托盘
记录成功或失败
```

### 8.2 每帧建议记录内容

```text
timestamp
front_camera_rgb
wrist_camera_rgb
front_depth，可选
tof_distance
joint_states
gripper_state
base_pose / odom
object_name
instruction
action
success_label
failure_reason
```

### 8.3 action 推荐格式

早期建议记录：

```text
[joint_delta_1, joint_delta_2, joint_delta_3, joint_delta_4, joint_delta_5, joint_delta_6, gripper_command]
```

后续可以尝试：

```text
[delta_x, delta_y, delta_z, delta_roll, delta_pitch, delta_yaw, gripper_command]
```

### 8.4 数据采集建议

先不要做“万能抓取”。

第一批数据只做：

```text
从桌面抓药瓶，放到车上托盘
```

建议采集：

```text
50 条成功 episode 起步
3 到 5 个不同药瓶
不同桌面位置
不同朝向
不同光照
少量干扰物
```

后续扩展：

```text
杯子
小盒子
遥控器
纸巾包
药盒
```

---

## 9. 当前已知进展

### 9.1 已经有明确设计的部分

* 项目定位清晰
* 端云协同架构清晰
* ROS2 总线思路清晰
* 语音到结构化任务方向清晰
* 底盘硬件链路基本明确
* 机械臂选型明确
* 助老递药 MVP 清晰
* 跌倒检测路线初步清晰

### 9.2 已经跑通或部分跑通的部分

```text
ASR → LLM → TTS 语音链路
SO-101 基础视觉伺服抓取
STM32 轮式里程计 + L1 2D scan 建图尝试
slam_toolbox 可运行
```

### 9.3 当前卡点

```text
L1 振动影响 IMU
2D scan 切片特征不足
slam_toolbox 回环失败
Nav2 尚未完整稳定
Voice Service 尚未完全 ROS2 节点化
视觉伺服近距离深度估计不准
SO-101 抓取精度仍需提升
广角相机、手眼相机、ToF 坐标系尚需打通
```

---

## 10. 下一步优先级

### P0：稳定底盘与 SLAM

目标：

```text
获得稳定 odom
获得可用地图
为 Nav2 提供可靠地基
```

任务：

```text
1. L1 减振安装
2. 检查 L1 IMU 数据
3. 录 rosbag2
4. 跑 Point-LIO
5. 接 slam_toolbox
6. 验证回环
```

验收标准：

```text
机器人低速绕实验室一圈
地图不明显撕裂
回到起点误差可接受
odom 连续且无明显跳变
```

---

### P1：Nav2 点到点导航

目标：

```text
机器人能从 A 点导航到 B 点
```

任务：

```text
1. 配置 Nav2
2. 设置 costmap
3. 调整 footprint
4. 调整 planner/controller
5. 低速导航测试
6. 添加急停和软限速
```

验收标准：

```text
RViz 中指定目标点
机器人能稳定到达
遇到障碍能停或绕行
不会明显撞击环境
```

---

### P2：Voice Service ROS2 节点化

目标：

```text
语音服务能真正驱动 ROS2 任务系统
```

任务：

```text
1. 封装 voice_service_node
2. 发布 /task_command
3. 定义任务 JSON schema
4. 接收 /task_status
5. TTS 播报任务结果
```

验收标准：

```text
用户说“去客厅桌子”
LLM 输出结构化任务
机器人任务中控收到任务
进入导航流程
```

---

### P3：抓取末端距离闭环

目标：

```text
解决单目视觉伺服最后距离不准的问题
```

任务：

```text
1. 安装 ToF / 激光测距模块
2. 发布 /tof/distance
3. 标定 tof_link
4. 将 ToF 接入抓取状态机
5. 设置接近速度和闭合阈值
```

验收标准：

```text
手眼相机完成 xy 对准
ToF 判断 z 距离
距离小于阈值后夹爪闭合
抓取成功率明显提升
```

---

### P4：语音到抓取全链路 MVP

目标：

```text
跑通完整助老递药流程
```

任务：

```text
1. 预设客厅桌子 nav_pose
2. 预设用户位置 nav_pose
3. 语音输入 fetch_object
4. Nav2 导航到桌前
5. 抓取药瓶
6. 放入托盘
7. 返回用户位置
8. TTS 播报完成
```

验收标准：

```text
连续 5 次任务
至少 3 次完整成功
失败时能明确播报失败原因
不会出现危险动作
```

---

### P5：跌倒检测与远程控制

目标：

```text
在 MVP 稳定后扩展助老应急能力
```

任务：

```text
1. 音频触发词检测
2. 撞击声检测
3. 声源定位
4. 导航靠近声源
5. YOLOv8-Pose 检测人体姿态
6. VLM 二次研判
7. 微信 / 飞书 / 电话报警
8. OpenClaw 远程自然语言控车
```

该阶段建议在递药 MVP 稳定后再做。

---

## 11. 风险与注意事项

### 11.1 不要让 LLM 直接控制底层动作

错误示例：

```text
LLM 输出 /cmd_vel
LLM 输出机械臂关节角
LLM 直接决定是否夹爪闭合
```

正确方式：

```text
LLM 输出任务意图
本地 ROS2 状态机分解动作
本地安全模块检查动作
底层控制器执行动作
```

---

### 11.2 不要过早追求复杂家务

当前优先级不是：

```text
开冰箱
拉抽屉
收衣服
擦桌子
逗猫
复杂双臂操作
```

当前优先级是：

```text
稳定导航
稳定识别
稳定抓轻物体
稳定返回
稳定播报
```

---

### 11.3 抓取不是检测问题

检测模型只负责找目标。
抓取系统还需要：

```text
目标 3D 位置
抓取点
接近方向
夹爪开合策略
距离判断
失败恢复
```

不要把 YOLO 检测成功误认为抓取系统已经完成。

---

### 11.4 先跑通，再优化

早期允许使用：

```text
AprilTag
预设地点
固定物体
固定桌面
固定光照
固定任务
```

目标是先验证系统闭环。
不要一开始追求开放世界泛化。

---

## 12. 建议仓库结构

建议后续拆分或整理为：

```text
ROS2_Eldercare_Robot/
├── README.md
├── PROGRESS.md
├── docs/
│   ├── hardware.md
│   ├── wiring.md
│   ├── ros2_architecture.md
│   ├── slam_nav2.md
│   ├── manipulation.md
│   ├── voice_service.md
│   └── roadmap.md
├── robot_description/
│   ├── urdf/
│   ├── meshes/
│   └── launch/
├── base_driver/
├── navigation/
├── perception/
├── manipulation/
├── voice_service_bridge/
├── task_orchestrator/
├── safety_manager/
├── configs/
│   ├── nav2/
│   ├── slam_toolbox/
│   ├── point_lio/
│   └── camera/
├── scripts/
└── bags/
```

---

## 13. 建议 README 展示结构

README 建议按照以下顺序展示：

```text
1. 项目一句话介绍
2. 当前能力 Demo
3. 系统架构图
4. 硬件清单
5. 软件模块
6. 当前进展
7. Roadmap
8. 如何复现
9. 子仓库链接
10. 已知问题
11. 贡献方式
```

当前项目最值得突出的是：

```text
ROS2 原生
端云协同
助老场景明确
真实 AGV 底盘
SLAM / Nav2 路线
SO-101 轻量抓取
语音任务编排
未来可接入多模态 Agent
```

---

## 14. 一句话项目画像

Project LINK / 灵犀 当前是一台以 Jetson Orin Nano 为端侧主机、STM32 + ZLAC8015D + 轮毂电机构成差速 AGV 底盘、Unitree L1 负责 SLAM 感知、SO-101 负责轻量桌面抓取、ASR/LLM/TTS 负责自然语言任务入口的 ROS2 助老移动操作机器人。

当前语音链路已独立跑通，SO-101 基础视觉伺服抓取可运行但近距离距离判断和抓取精度仍需补强，底盘建图可跑但回环不稳，下一步重点是 Point-LIO 稳定里程计、Nav2 全栈调通、Voice Service ROS2 节点化、末端 ToF / 激光测距接入，然后跑通“语音 → 导航 → 抓取 → 返回”的端到端递药 MVP。

---

## 15. 推荐近期执行清单

### 本周优先

```text
[ ] 固定 L1，做减振处理
[ ] 录制静态/直线/旋转 rosbag2
[ ] 检查 L1 IMU 和点云质量
[ ] 跑通 Point-LIO 初版
[ ] 设计 /task_command JSON schema
[ ] 明确 Nav2 地点数据库格式
```

### 下一阶段

```text
[ ] 接入 Nav2 点到点导航
[ ] Voice Service 发布 /task_command
[ ] 抓取模块封装为 GraspObject Action
[ ] 安装末端 ToF / 激光测距
[ ] 建立 base_link、arm_base、wrist_camera、tof_link 的 TF
[ ] 用 AprilTag 调通预抓取流程
```

### MVP 阶段

```text
[ ] 预设“客厅桌子”操作位
[ ] 预设“用户身边”返回点
[ ] 语音触发 fetch_object
[ ] 导航到桌前
[ ] 抓取药瓶
[ ] 放入托盘
[ ] 返回用户
[ ] TTS 播报完成
[ ] 连续多次测试并记录失败原因
```

---

## 16. 交接结论

当前项目已经具备清晰的系统愿景和较完整的硬件/软件模块规划，但还处于关键集成阶段。后续开发的核心策略应该是：

```text
先稳定底盘定位
再跑通 Nav2
再接入语音任务
再补强抓取距离闭环
最后完成递药 MVP
```

不要在当前阶段继续扩展过多复杂功能。第一条可演示、可复现、可多次运行的闭环，比十个半成品模块更有价值。

最重要的工程主线：

```text
SLAM / odom 稳定
→ Nav2 稳定
→ 任务中控稳定
→ 抓取闭环稳定
→ 端到端递药稳定
```
