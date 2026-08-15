# Windows 机械臂一体化测试台

该工具直接在 Windows 上独占并测试：

- USB 摄像头与 YOLO-World；
- SO-101 串口、扭矩、五个机械臂关节和夹爪；
- Windows GUI 内完整 LeRobot 中位、全行程和夹爪校准；
- standby、pregrasp、placement 三个预设姿态；
- YOLO 视觉卸力示教 CSV，包含关节、bbox 中心/面积、误差、可信状态和 ToF；
- ESP32-C3 + VL53L0X 串口、频率、距离和拒绝帧；
- 目标跟踪、视觉居中、逼近、ToF 影子模式和距离闭环抓取；
- 停止运动与关闭扭矩。
- 每次预设运动的命令、反馈、校准和电机寄存器 JSONL 调试日志。

它不依赖 ROS 2。启动前关闭原 `VisualTracker`、VL53L0X Monitor 和其他可能打开
相同 COM 口或摄像头的程序。

## 启动

从仓库根目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_windows_visual_grasp_lab.ps1
```

脚本优先使用：

```text
%USERPROFILE%\Desktop\机器人项目\VisualTracker\venv\Scripts\python.exe
```

只检查依赖：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_windows_visual_grasp_lab.ps1 -CheckOnly
```

离屏创建完整 GUI、但不连接硬件：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_windows_visual_grasp_lab.ps1 -SmokeTest
```

## 最快安全测试顺序

1. 不连接机械臂，连接摄像头和 ToF，确认视频、YOLO 模型和 15 到 20 Hz 测距。
2. 输入目标并开始跟踪，只看框，不启用扭矩。
3. 首次使用或提示校准缺失时，在“关节、姿态与示教”页完成三步 LeRobot 校准。
4. 重启后连接 SO-101，读取六关节；确认校准可加载和读数合理后才启用扭矩。
5. 小步发送五关节目标，再独立测试夹爪开合。
6. 录制待机、待抓取和放置位，逐个执行并确认运动方向。
7. 需要重建逼近轨迹时，先让绿色 YOLO 框稳定，再执行一次完整视觉卸力示教并提交 CSV。
8. 第一版自动抓取会显示 `FINAL_APPROACH`：进入该状态后 YOLO 消失属于预期，但 ToF
   必须持续有效；任何 ToF 失效、超时或盲走超限都会停止。
7. 保持 `ToF 控制=false`、`已标定=false`，运行影子观察。
8. 用卷尺测量夹爪接触点距离，填写 `tof_grasp_distance_m` 并保存。
9. 确认至少 10 次影子判断正确后，勾选“已现场标定”和“ToF 控制”。
10. 低速测试自动抓取；拔掉 ToF 时控制器必须进入 `RANGE_WAIT` 并停止逼近。

操作语义固定为：扭矩关闭时录制姿态，扭矩开启时执行姿态；“开始跟踪”只显示目标框，
不会移动机械臂；“开始自动抓取”会打开夹爪并自动执行
`pregrasp → 居中 → 逼近 → 夹取`。抓取完成后再手动执行 `placement → 打开夹爪 → standby`。

任何方向、姿态或距离不符合预期时，立即点击“紧急停止并关闭扭矩”。该按钮不替代
物理断电或急停。

运行时配置、姿态和示教数据保存在：

```text
%APPDATA%\ProjectLINK\visual_grasp_lab\
```

视觉示教文件位于 `demos\visual_demo_YYYYMMDD_HHMMSS.csv`。每行记录时间、目标文本、
画面尺寸、期望中心、bbox 坐标/中心/面积比例、归一化误差、YOLO `trusted/sequence`、
ToF 距离和六个关节反馈，可直接用于拟合当前相机视角下的水平逼近轨迹。

“参数与日志”页默认启用详细调试日志。文件保存在：

```text
%APPDATA%\ProjectLINK\visual_grasp_lab\logs\visual_grasp_debug_*.jsonl
```

它记录本次配置、三个预设、LeRobot 校准范围、逐周期目标/反馈/误差、完成判定，以及
控制器报错瞬间的 `Present_Position`、`Goal_Position`、速度、负载、电流、温度、Moving、
Status 和扭矩寄存器。日志不保存摄像头画面或模型文件。问题复现后退出程序，把时间最新
的 `.jsonl` 文件直接发给维护者即可。

从首次安装、LeRobot 校准、预设位录制、YOLO 调整、ToF 标定到第一次监督抓取的完整
步骤见 `docs/WINDOWS_VISUAL_GRASP_INITIALIZATION_TUTORIAL.md`，也可以点击 GUI 顶部
“打开 Windows 完整教程”。
