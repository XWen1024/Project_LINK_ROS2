# YOLO World Visual Grasp Deployment

## 范围与安全

该栈只进行二维图像空间视觉伺服。它不会移动底盘、发布 `/cmd_vel`、启动 Nav2、增加
避障，也不会替换当前 SLAM/TF 栈。启用扭矩或开始抓取前，必须清空机械臂周围的人员和
线缆，并准备物理断电或急停。

## Orin 部署

1. 通过 Git 更新仓库并构建新包：

   ```bash
   cd /home/wte/wheeltec_robot
   source /opt/ros/humble/setup.bash
   rosdep install --from-paths src --ignore-src -r -y
   colcon build --symlink-install --packages-select wheeltec_robot_msg project_link_visual_grasp project_link_visual_grasp_gui
   ```

2. 在能够导入 `rclpy` 的 Orin Python 环境安装 Jetson 兼容的 PyTorch/CUDA、
   `ultralytics`、`opencv-python`、`numpy`、`PyYAML` 和 `lerobot`；运行
   `python3 -c 'import torch, ultralytics, cv2, lerobot'` 验证。
3. 将现有 `yolov8s-worldv2.pt` 放入 `/home/wte/models/yolov8s-worldv2.pt`；模型文件
   不提交到 Git。
4. 为摄像头与 SO-101 建立稳定设备名。默认值为 `/dev/RgbCam` 和 `/dev/so101`；可先改
   `configs/visual_grasp/visual_grasp.yaml`，或由 GUI 远程调参。
5. 启用扭矩前执行：

   ```bash
   source /home/wte/wheeltec_robot/scripts/project_link_env.sh
   v4l2-ctl --device=/dev/RgbCam --all
   ls -l /dev/so101
   python3 -c 'from ultralytics import YOLO; YOLO("/home/wte/models/yolov8s-worldv2.pt"); print("model ok")'
   ```

## 启动 Orin 和 Ubuntu

需要 SLAM 时先单独启动 SLAM；视觉节点不会加入 `start_slam_tmux.sh`：

```bash
cd /home/wte/wheeltec_robot
./start_slam_tmux.sh --restart
./scripts/start_visual_grasp_tmux.sh --restart
```

Ubuntu 显示电脑构建并 source 相同项目包，安装 `PySide6`、OpenCV、NumPy 后运行：

```bash
source /home/wte/wheeltec_robot/scripts/project_link_env.sh
ros2 run project_link_visual_grasp_gui visual_grasp_gui
```

GUI 通过 ROS 2 心跳自动发现 Orin；命名空间手动输入仅作回退。所有控制走 DDS，不创建
按 IP 的自定义 TCP 控制协议。

## 恢复

- 使用“停止运动”停止当前视觉伺服，同时保留跟踪。
- 手动触碰机械臂前关闭扭矩。
- 删除 Orin 的 `~/.config/project_link/visual_grasp/overrides.yaml` 可恢复仓库默认参数。
- 只有在确定不再需要已录制姿态时，才删除运行时 `positions.json`。