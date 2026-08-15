# SO-101 视觉抓取从初始化到抓取教程

本文用于 Project LINK 现场首次部署和重新校准。Orin 运行无头视觉抓取、LeRobot
SO-101 和 VL53L0X；Ubuntu 电脑只运行远程 GUI。

## 1. 安全准备

- 底盘停止，不运行键盘遥控、Nav2 目标或其他 `/cmd_vel` 测试。
- 机械臂周围清空人员、线缆和硬物，准备物理断电或急停。
- 校准期间机械臂会卸力，必须用手托住，防止突然下坠。
- Windows VL53L0X 台架 GUI 必须关闭，ESP32 串口只能由 Orin ROS 节点拥有。

## 2. Orin 构建与依赖

```bash
ssh wte@orin
cd /home/wte/wheeltec_robot
git pull --ff-only
source /opt/ros/humble/setup.bash
sudo apt install python3-serial

colcon build --packages-select \
  wheeltec_robot_msg \
  project_link_vl53l0x \
  project_link_visual_grasp \
  project_link_visual_grasp_gui

source install/setup.bash
```

确认 Python 环境：

```bash
python3 -c 'import rclpy, torch, ultralytics, cv2, lerobot, serial; print("python ok")'
python3 -c 'import torch; print(torch.cuda.is_available())'
```

## 3. 硬件和配置检查

```bash
ls -l /dev/RgbCam
ls -l /dev/so101
ls -l /dev/vl53l0x-gripper
ls -l /home/wte/models/yolov8s-worldv2.pt
```

视觉抓取配置：

```text
/home/wte/wheeltec_robot/configs/visual_grasp/visual_grasp.yaml
```

VL53L0X 配置：

```text
/home/wte/wheeltec_robot/configs/vl53l0x/vl53l0x_gripper.yaml
```

首次启动保持：

```yaml
tof_enabled: false
tof_control_enabled: false
tof_calibrated: false
```

## 4. 启动 Orin

```bash
cd /home/wte/wheeltec_robot
source scripts/project_link_env.sh
./scripts/start_visual_grasp_tmux.sh --restart --with-tof
```

只读检查：

```bash
ros2 topic echo /visual_grasp/status --once
ros2 topic echo /visual_grasp/tof_status --once
ros2 topic hz /visual_grasp/image/compressed
ros2 topic hz /visual_grasp/tof_range
```

此时应看到模型和相机就绪。机械臂尚未校准或连接时可以显示未连接。

## 5. 启动 Ubuntu GUI

Ubuntu 使用与 Orin 相同的 ROS 2 域：

```bash
source /你的工作区/install/setup.bash
export ROS_DOMAIN_ID=42
export ROS_LOCALHOST_ONLY=0
ros2 run project_link_visual_grasp_gui visual_grasp_gui
```

在“设备连接”区域选择自动发现的 `/visual_grasp`。刷新参数按钮在第一行，命名空间
应用按钮在第二行；SO-101 扭矩开关位于机械臂控制区的独立一行。

## 6. 第一次 LeRobot SO-101 校准

校准不使用 Orin 终端的 `input()`，全部由 Ubuntu GUI 远程推进。

1. 用手托住机械臂，点击“1. 开始校准并卸力”。
2. 确认 GUI 状态为 `WAIT_MIDDLE`，所有关节应处于无扭矩状态。
3. 把肩部、肘部、腕部和夹爪分别摆到各自机械行程的中间位置。夹爪保持半开。
4. 点击“2. 记录中位”。状态应变为 `RECORDING_RANGE`。
5. 逐个关节缓慢走遍完整且安全的机械行程。每个关节至少往返一次，夹爪也必须完成
   全开到全闭。不要撞击机械限位。
6. 所有关节都覆盖完整行程后，点击“3. 完成全行程记录”。
7. GUI 显示 `READY` 和校准文件路径后，点击“连接”。状态中的“校准”应显示“有效”。
8. 最后才勾选“启用扭矩”。

若提示某个关节没有移动，点击“取消校准”，重新开始并确保该关节走过有效范围。
校准期间不要点击开始抓取、预设姿态或夹爪控制。

## 7. 测试夹爪和录制预设姿态

先小范围测试夹爪，不要直接使用极限值：

1. 在夹爪输入框设置中间值并点击“设置夹爪”。
2. 逐步验证 `gripper_open` 和 `gripper_close` 的方向。
3. 手动或低速移动到安全待机姿态，点击待机位“录制”。
4. 录制待抓取位和放置位。
5. 分别点击三个“前往”，确认运动方向和电缆余量。

预设姿态保存在 Orin：

```text
~/.config/project_link/visual_grasp/positions.json
```

## 8. 只测试 YOLO World 跟踪

1. 输入目标文本，例如 `medicine bottle` 或 `red cup`。
2. 点击“开始跟踪”，暂时不要点击“开始抓取”。
3. 确认画面中的框稳定跟随正确物体，状态为 `TRACKING`。
4. 必要时调整 `yolo_conf_threshold`、`center_offset_x` 和
   `center_offset_y`。

## 9. VL53L0X 影子模式与标定

在参数区设置：

```yaml
tof_enabled: true
tof_control_enabled: false
tof_calibrated: false
```

确认 GUI 的“末端 ToF”持续显示距离、数据年龄和 `OBSERVE`/`WOULD_GRASP`。使用卷尺在
多个距离点对比读数，然后把机械臂缓慢摆到夹爪刚好可以闭合的位置，记录至少 20 帧
稳定距离的中值。该值经过保守余量后写入 `tof_grasp_distance_m`。

至少完成 10 次影子判断，确认 `WOULD_GRASP` 时机正确后，才设置：

```yaml
tof_calibrated: true
tof_control_enabled: true
```

## 10. 第一次监督抓取

1. 前往待抓取位。
2. 确认目标框稳定、ToF 状态有效、距离变化方向正确。
3. 保持人员在物理急停旁，点击“开始抓取”。
4. 距离无效或过期时，状态应进入 `RANGE_WAIT`，机械臂不得继续逼近。
5. 达到标定距离后，机械臂停止前进并闭合夹爪。
6. 使用“停止运动”可以中止当前逼近；需要人工接触机械臂时关闭扭矩。

连续完成 3 次低速抓取后，才测试自动 Action：

```bash
ros2 action send_goal \
  /visual_grasp/track_and_grasp \
  wheeltec_robot_msg/action/TrackAndGrasp \
  "{target: 'medicine bottle', timeout_sec: 45.0}" \
  --feedback
```

## 11. 常见故障

- “校准缺失或不匹配”：使用 GUI 完成四步 LeRobot 校准，不要反复点击连接。
- `WAIT_MIDDLE`：机械臂已卸力，摆到所有关节中位后点击记录中位。
- “某关节没有移动”：重新校准并让该关节完整往返。
- `RANGE_WAIT`：检查 `/visual_grasp/tof_range`、USB、传感器遮挡和 stale 时间。
- GUI 看不到 Orin：检查两端 `ROS_DOMAIN_ID=42`、`ROS_LOCALHOST_ONLY=0` 和防火墙。
- 图像正常但不识别：检查模型路径、目标英文描述和置信度阈值。
- 需要完全重置视觉参数：删除
  `~/.config/project_link/visual_grasp/overrides.yaml` 后重启节点。

任何校准、距离或运动方向不确定时，保持扭矩关闭并停止抓取测试。
