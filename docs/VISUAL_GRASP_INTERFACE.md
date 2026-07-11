# Visual Grasp ROS 2 Interface

Orin 上的 `project_link_visual_grasp` 节点独占摄像头、YOLO-World 模型和 SO-101
串口。Ubuntu 上的 GUI 只是 ROS 2 客户端。两台机器必须使用相同的
`ROS_DOMAIN_ID=42` 与 `ROS_LOCALHOST_ONLY=0`。

## Topics

| 名称 | 类型 | 用途 |
| --- | --- | --- |
| `/visual_grasp/image/compressed` | `sensor_msgs/CompressedImage` | Orin 标注 YOLO 框和状态后的 JPEG 图像。 |
| `/visual_grasp/status` | `wheeltec_robot_msg/VisualGraspStatus` | 执行状态、目标、硬件健康、检测框、关节和错误。 |
| `/project_link_visual_grasp/discovery` | `wheeltec_robot_msg/VisualGraspStatus` | GUI 设备自动发现心跳。 |

## 手动控制服务

| 名称 | 类型 | 用途 |
| --- | --- | --- |
| `/visual_grasp/set_target` | `wheeltec_robot_msg/SetTarget` | 设置 YOLO-World 文本类别并开始不运动的跟踪。 |
| `/visual_grasp/set_gripper` | `wheeltec_robot_msg/SetGripper` | 设置独立夹爪位置。 |
| `/visual_grasp/connect_arm`、`/visual_grasp/disconnect_arm` | `std_srvs/Trigger` | 连接或断开 SO-101。 |
| `/visual_grasp/set_torque` | `std_srvs/SetBool` | 开启或关闭扭矩。 |
| `/visual_grasp/start_approach` | `std_srvs/Trigger` | 操作员确认后执行居中、逼近和夹取。 |
| `/visual_grasp/stop` | `std_srvs/Trigger` | 停止逼近或预设姿态运动，保留跟踪。 |
| `/visual_grasp/record_{standby,pregrasp,placement}` | `std_srvs/Trigger` | 在 Orin 保存当前姿态。 |
| `/visual_grasp/go_{standby,pregrasp,placement}` | `std_srvs/Trigger` | 以非 `shoulder_pan` 关节优先的方式前往已保存姿态。 |
| `/visual_grasp/start_demo_recording`、`/visual_grasp/stop_demo_recording` | `std_srvs/Trigger` | 开始或保存无控制器运动的示教 CSV。 |

使用标准 ROS 2 参数服务可远程调整所有节点参数。接受的修改即时应用，并保存到 Orin
的 `~/.config/project_link/visual_grasp/overrides.yaml`；运行时不会改写仓库 YAML。

## 导航调度 Action

`/visual_grasp/track_and_grasp` 的类型为
`wheeltec_robot_msg/action/TrackAndGrasp.action`：

```text
Goal:     string target, float32 timeout_sec
Result:   bool success, string final_state, string message
Feedback: string state, string message, float32 confidence
```

Action 会设置目标、等待检测结果，并执行与 GUI 手动抓取相同的视觉伺服序列。其结果为
`GRASPED`、`CANCELED`、`TARGET_NOT_FOUND`、`TIMEOUT` 或 `ERROR`/`HARDWARE_ERROR`。

```python
from rclpy.action import ActionClient
from wheeltec_robot_msg.action import TrackAndGrasp

client = ActionClient(node, TrackAndGrasp, "/visual_grasp/track_and_grasp")
client.wait_for_server()
goal = TrackAndGrasp.Goal()
goal.target = "red cup"
goal.timeout_sec = 45.0
future = client.send_goal_async(goal)
# 导航成功且底盘到达安全抓取位后，再根据 action 结果继续放置流程。
```

在确认机械臂预设姿态、物体位置和周围空间安全之前，不得调用此 action。