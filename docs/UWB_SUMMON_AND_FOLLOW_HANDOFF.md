# UWB 召唤与跟随迁移交接

本文档定义从 `human-chocker-and-robot-dog/mcp-for-UWB` 迁移到 Project LINK
ROS 2 Humble、Point-LIO、slam_toolbox 和 Navigation2 的实现边界、启动方式、
标定门禁和现场验收顺序。

## 1. 当前结论

- 上游仓库没有完成的 UWB 跟随程序，提供的是 BU03/BU04 协议设计、DimOS
  MCP 服务和一份实现计划。本仓库已按 ROS 2/Nav2 重新实现第一版。
- UWB 层只估计佩戴者位置并提交高层 Nav2 目标，不发布 `/cmd_vel`。
- Nav2 仍是唯一运动路径，沿用现有 Point-LIO 位姿、C63A `/odom` 速度反馈、
  `/scan_accumulated` 障碍物和 `0.18 m/s` 最大线速度。
- 召唤与跟随共用 `/uwb_navigation/person_navigation` Action：
  - 召唤：只计算并提交一个静态 Nav2 目标，机器人到佩戴者附近约 `1.0 m`
    后完成并停车；不得使用跟随模式的周期刷新取消该目标。
  - 跟随：机器人保持约 `1.5 m`，在 `1.3..1.7 m` 区间内停车等待，并可按
    位移/时间门限滚动替换 Nav2 目标。
- 人离得过近时只取消 Nav2 目标并等待，不生成倒车目标。
- UWB/TF 过期、Nav2 拒绝、取消超时、串口断开或出现额外 `/cmd_vel`
  发布者时，任务失败关闭并取消当前 Nav2 目标。
- 相邻 map 目标推算速度默认不得超过 `3.0 m/s`；超限按跳点处理并停车。
- 默认是 shadow 模式。仓库配置中的标定状态故意为 `invalid`，不允许直接
  开启实机运动。
- BU04 有两个 Type-C。丝印 `USB` 的端口是 STM32F103 原生 CDC
  `0483:5740`，必须作为 ROS 测距流 `/dev/uwb-bu04`；丝印 `TTL` 的端口是
  CH340 `1a86:7523`，只作为 AT/配置口 `/dev/uwb-bu04-at`。为 Orin
  `5.15.185-tegra` 安装的 CH341 模块仍用于后者，不能再把 raw `ttyUSB` 编号
  或 CH340 本身当成测距设备身份。
- 官方 AT V1.0.6 查询确认 PDoA、基站角色、JSON、100 ms 输出周期、滤波和
  一个配对标签均已配置。异常项 `AncID:65535` 已按官方范围修复为 `AncID:1`，
  保持网络 `0x1111`，执行 `AT+SAVE` 后通过物理断电重连验证。冷启动报告
  `slot:1,addr:<redacted>`、基站配置成功且五个错误位均为 0。
- CH340 `COM25` 的 90 秒认证/重连、JSON 和临时 Hex 测试均无连续测距字节，
  是因为它连接的是丝印 `TTL` 的 AT 口。换到丝印 `USB` 的原生 STM32 CDC 后，
  Windows 枚举为 `COM26`，无需发送 AT 即立即输出协议规定的
  `JS + 4 hex length + JSON`。10 秒捕获 289 帧，289 帧全部解析成功，约
  `28.9 Hz`。因此不需要重刷 V1.0.0 固件。
- 换回 Orin 后原生 USB 枚举为 `/dev/ttyACM1`、`0483:5740`，物理路径正是
  `platform-3610000.usb-usb-0:2.3:1.0`。现有解析器 10 秒收到 34,110 字节，
  解析 288 帧且 288 帧全部合法，约 `28.8 Hz`，无断连。新 udev 规则与实机身份
  完全匹配，但仍需按 GitHub 更新流程部署后再验证稳定别名。

## 2. ROS 2 数据流

```text
BU03 tag worn by person
-> BU04 dual-antenna PDoA
-> USB serial JS + 4 hex length + JSON payload
-> uwb_serial_node
-> /uwb/person_observation
-> calibration: BU04 axes/mount -> base_footprint
-> map -> base_footprint TF from Point-LIO/slam_toolbox
-> person point in map
-> summon/follow holding policy
-> /uwb_navigation/proposed_goal
-> NavigateToPose
-> existing NavFn + DWB + velocity_smoother
-> /cmd_vel
-> C63A base
```

替换上游 DimOS 术语后的对应关系：

| 上游设计 | Project LINK 实现 |
|---|---|
| DimOS SLAM pose | `map -> base_footprint` TF |
| DimOS high-level navigation | Nav2 `NavigateToPose` |
| DimOS local stop/cancel | Nav2 Action cancel acknowledgement |
| DimOS command owner | `velocity_smoother` 是允许的 `/cmd_vel` 发布者 |
| MCP skill | stdio FastMCP 工具调用 ROS 2 Action/Service |

## 3. 新增包和接口

```text
project_link_uwb_interfaces
  msg/UwbObservation.msg
  action/PersonNavigation.action

project_link_uwb_navigation
  framing.py       有界 BU04 字节流解码
  protocol.py      JSON、tag、距离和时钟严格校验
  geometry.py      安装标定和 map 坐标变换
  policy.py        召唤/跟随距离策略与目标节流
  serial_node.py   只读 BU04 USB 串口节点
  nav2_server.py   UWB 到 NavigateToPose Action 服务器
  mcp_server.py    可选 stdio MCP 高层工具
```

主要端点：

```text
/uwb/person_observation
/uwb/status
/uwb_navigation/proposed_goal
/uwb_navigation/status
/uwb_navigation/person_navigation
/uwb_navigation/stop
```

## 4. BU04 协议

第一版只接受上游已经记录的 JSON 帧：

```text
JS006D{"TWR":{"a16":"4096","T":1490981,"D":37,
"Xcm":14,"Ycm":32,...}}
```

规则：

- `JS` 后必须是 4 个 ASCII 十六进制长度字符。
- 严格读取声明长度，不依赖换行。
- 载荷上限默认 `4096 bytes`，噪声缓存有界。
- 必需字段：`a16`、`T`、`D`、`Xcm`、`Ycm`。
- `a16` 必须匹配本地环境变量配置的唯一 tag。
- `T` 必须严格递增；重复或倒退数据不能刷新 TTL。
- `D` 与 `hypot(Xcm,Ycm)` 的残差必须低于标定阈值。
- 运行时不发送 AT 命令、不执行 `AT+SAVE`、不扫描波特率、不刷固件。

私有 tag 地址只放在 Orin 本地环境：

```bash
export PROJECT_LINK_UWB_TAG_ADDRESS='<private-a16>'
export PROJECT_LINK_UWB_DEVICE=/dev/uwb-bu04
```

不要把地址、USB 序列号或 udev 私有匹配值提交到 Git。

## 5. 构建与依赖

```bash
cd /home/wte/wheeltec_robot
source /opt/ros/humble/setup.bash
sudo apt install python3-serial
colcon build --packages-select \
  project_link_uwb_interfaces project_link_uwb_navigation
source install/setup.bash
```

当前 Orin 的 `setuptools 82` 与 Humble colcon 的 Python `--symlink-install`
路径不兼容，会报 `setup.py develop --editable`。这两个 UWB 包使用上面的普通安装
构建；不要为了它们降级系统 Python 环境。

MCP 是可选依赖，建议放在项目虚拟环境中：

```bash
python3 -m venv ~/.venvs/project-link-uwb-mcp
~/.venvs/project-link-uwb-mcp/bin/pip install -r \
  src/project_link_uwb_navigation/requirements-mcp.txt
```

## 6. 标定文件

先复制示例配置到 Orin 本地目录：

```bash
mkdir -p ~/.config/project_link
cp src/project_link_uwb_navigation/config/uwb_navigation.yaml \
  ~/.config/project_link/uwb_navigation.yaml
chmod 600 ~/.config/project_link/uwb_navigation.yaml
```

必须实测并填写：

```yaml
calibration_status: proposed  # shadow 验证时
calibration_version: "mount-v1-<hash>"
axis_xx: 1.0
axis_xy: 0.0
axis_yx: 0.0
axis_yy: 1.0
sensor_yaw_rad: 0.0
sensor_translation_x_m: 0.0
sensor_translation_y_m: 0.0
max_range_residual_m: 0.50
```

上面的矩阵和值只是字段格式，不是本车标定结果。BU04 最终固定后，在机器人
前、后、左、右 `2 m` 各采集至少 100 帧，并在前方 `1.0/1.5/2.0/3.0 m`
采集距离点，确定：

- `Xcm/Ycm` 对应前后/左右的轴和符号；
- BU04 原点相对 `base_footprint` 的平移与安装偏航；
- 正后方和身体遮挡下的有效扇区；
- 帧率、中位/95 分位间隔、丢帧率；
- `D` 与坐标距离残差的拒绝阈值；
- UWB TTL 是否需要大于默认 `0.50 s`。

只有 shadow 行走轨迹确认没有镜像、90 度错轴或固定偏移后，才把状态改为：

```yaml
calibration_status: valid
```

## 7. Shadow 模式

先启动已验证的 Navigation Two：

```bash
./navigation_two_start_navigation.sh --restart
```

再启动 UWB shadow。它读取真 UWB 和 TF、发布 map 目标，但不调用 Nav2：

```bash
export PROJECT_LINK_UWB_TAG_ADDRESS='<private-a16>'
./navigation_two_start_uwb.sh --shadow \
  --device /dev/uwb-bu04 \
  --params ~/.config/project_link/uwb_navigation.yaml \
  --restart
```

单标签现场标定或 BU04 断电重启后，也可以使用仓库内的长期 shadow-only
入口。它验证 STM32 USB `0483:5740`，只接受一个观测到的标签，地址仅保留在
进程环境中且不会打印或写盘：

```bash
python3 scripts/start_uwb_shadow_auto_tag.py \
  --device /dev/ttyACM1 \
  --params ~/.config/project_link/uwb_navigation.yaml \
  --restart
```

该入口没有 live 参数，不能启用运动。正式 live 仍必须显式提供私有标签、有效
标定文件和 `UWB-NAV2` 操作员确认。

Shadow 原始观测启动不依赖底盘、雷达、SLAM、`/map` 或 Nav2，可独立用于前后
左右数据采集。没有 `map -> base_footprint` TF 时，`/uwb/person_observation`
仍正常发布，但 summon/follow 不会伪造 map 目标；只有 live 模式检查完整的
Navigation Two 前置条件。

启动器先创建 bootstrap window，把私有 tag 和设备路径写入 tmux session，再创建
真正的 `uwb` window，避免已经启动的 shell 读取不到环境。ready 门等待持续的
`/uwb/person_observation`，不依赖可能被晚订阅者错过的一次性状态消息。

测试召唤目标：

```bash
./navigation_two_uwb.sh summon
```

测试连续跟随目标：

```bash
./navigation_two_uwb.sh follow
```

`follow` 会持续显示 Action feedback。请在第二个终端停止：

```bash
./navigation_two_uwb.sh stop
```

RViz Fixed Frame 使用 `map`，增加 `PoseStamped` 显示：

```text
/uwb_navigation/proposed_goal
```

佩戴者在已知路线行走时，目标点必须与真实方向一致，不能镜像或旋转 90 度。

## 8. Live 模式

Live 启动不会自动发送召唤或跟随目标，但会本地解锁 Action 向 Nav2 提交目标：

```bash
./navigation_two_start_uwb.sh \
  --enable-motion \
  --confirm-motion UWB-NAV2 \
  --device /dev/uwb-bu04 \
  --params ~/.config/project_link/uwb_navigation.yaml \
  --restart
```

启动脚本会拒绝：

- Navigation Two 关键 topic 或 `/navigate_to_pose` 缺失；
- BU04 稳定设备路径不存在；
- 私有 tag 地址未通过环境变量提供；
- 标定文件不存在或不是 `calibration_status: valid`；
- 键盘遥控、直接 A/B、`ab_drive_server` 或 LLM demo 速度节点仍运行；
- 缺少明确的 `UWB-NAV2` 本地确认 token。

确认空旷场地、急停和断电人员后，按顺序测试：

```bash
./navigation_two_uwb.sh summon
./navigation_two_uwb.sh stop
./navigation_two_uwb.sh follow
```

跟随期间使用另一个终端：

```bash
./navigation_two_uwb.sh stop
```

当前 Nav2 最大线速度为 `0.18 m/s`，低于上游设计的 `0.4 m/s` 上限。不要为了
UWB 跟随单独提高 Nav2 速度；先完成低速验收。

## 8.1 录包与回放

标定和 shadow 行走数据留在 Orin 本地，不提交 Git：

```bash
mkdir -p ~/project_link_data/uwb
ros2 bag record -o ~/project_link_data/uwb/front_2m \
  /uwb/person_observation /uwb/status /uwb_navigation/proposed_goal \
  /tf /tf_static /odom_lio
```

依次记录 `front_2m`、`rear_2m`、`left_2m`、`right_2m` 和各距离点。离线回放时
保持 UWB 节点处于 shadow，并且不要启动 live Nav2 任务：

```bash
ros2 bag play ~/project_link_data/uwb/front_2m
```

## 9. MCP 工具

可选 stdio MCP 服务只暴露：

```text
uwb_get_person_navigation_status
uwb_summon_robot
uwb_start_following
uwb_stop_person_navigation
```

启动方式：

```bash
source /opt/ros/humble/setup.bash
source /home/wte/wheeltec_robot/install/setup.bash
~/.venvs/project-link-uwb-mcp/bin/python -m \
  project_link_uwb_navigation.mcp_server
```

MCP 不提供 arm、速度、倒车、固件或串口配置工具。若 ROS 节点处于 shadow、
标定无效、Nav2 不健康或存在命令所有者冲突，MCP 请求会被本地 ROS 层拒绝。

## 10. 故障行为

| 故障 | 行为 |
|---|---|
| UWB 超过 TTL | 取消 Nav2 目标，Action 失败关闭 |
| TF 超过 TTL/不可用 | 取消 Nav2 目标，Action 失败关闭 |
| 串口拔出 | observation 不再刷新，随后 TTL 停止 |
| 错 tag/坏 JSON/时间倒退 | 丢弃帧，不刷新 TTL |
| 人过近 | 取消目标并保持，不生成倒车目标 |
| Nav2 拒绝/失败 | Action 失败关闭 |
| 取消无确认 | 禁止提交替换目标，Action 失败关闭 |
| 额外 `/cmd_vel` 发布者 | 拒绝或中止 UWB 任务 |
| 用户 stop/cancel | 取消当前 Nav2 目标 |

第一版没有启用自动丢失搜索。丢失后直接停车并结束任务，比未经标定的原地搜索
更安全。等正后方 PDoA、Nav2 `Spin` 取消语义和现场旋转空间验证后，再增加最多
一次、最多 10 秒的高层搜索，不得直接发布角速度。

## 11. 现场验收门禁

1. 串口门：稳定 `/dev/uwb-bu04`，拔插可复现，帧率和坏帧率已记录。
2. 静态标定门：四方向和四距离数据完成，标定仍不自动批准。
3. Shadow 门：人在地图内行走，目标方向、距离和 TF 时间一致。
4. 召唤门：最低速度、开阔场地，先验证 stop、USB 拔出和遥控接管。
5. 跟随门：直线、缓弯、人员停止、重新远离、遮挡、不可达目标逐项测试。
6. 耐久门：Point-LIO lag 保持 `<0.2 s`，UWB 无单调积压，连续运行 30 分钟。

任何一次停止、取消、避障或人工接管失败都会关闭 live 门禁。没有完成上述实机
证据前，只能说代码和离线策略已完成，不能宣称召唤/跟随已经在实车上验收。

2026-08-06 的第一次 live 召唤证明 Nav2 已接收目标并让底盘短暂运动，但共享的
`0.75 s` 跟随刷新错误地取消了召唤目标，约 `0.83 s` 后即停止。修复后 summon
每次 Action 只允许提交一个静态目标，follow 才能滚动替换；该召唤门仍需重新实测。

Windows 和 Orin 原生 USB 的持续测距帧门都已经通过。下一步按 GitHub 更新流程
部署 udev 规则，确认 `/dev/uwb-bu04` 对应 `0483:5740`，再运行 ROS 串口节点和
Nav2-free shadow 检查。ROS shadow 通过前仍不进入坐标标定或 live motion。
