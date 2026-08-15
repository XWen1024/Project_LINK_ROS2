# ESP32-C3 + VL53L0X USB 测距与 ROS 2 接入交接

本文档记录 ESP32-C3 + VL53L0X 测距桥的当前已验证状态、接线、固件和
Windows 上位机使用方法，并定义把它接入 Project LINK ROS 2 Humble 环境的
推荐实现。

## 1. 当前结论

- 验证日期：2026-08-10。
- 控制器：ESP32-C3 revision v1.1，4 MB Flash。
- USB：ESP32-C3 原生 USB Serial/JTAG，Windows 当前枚举为 COM21；
  COM 编号不是稳定身份，不能写死到 Linux/Orin 配置。
- USB 身份：VID:PID 为 303a:1001。实际 Linux 串口预计为 ttyACM 设备，
  必须通过 udev 建立稳定别名后再交给 ROS 2。
- 雷达：VL53L0X，默认 I²C 地址 0x29。
- 固件默认 I²C：GPIO4 为 SDA，GPIO5 为 SCL，400 kHz。
- 固件默认输出周期：50 ms，目标约 20 Hz。
- 固件控制台：原生 USB Serial/JTAG 主控制台。
- 实机已经收到有效帧 DATA,2,203,43,0，即 43 mm、状态 0。
- 固件、启动日志、Flash 写入和镜像 Hash 校验均已通过。
- 当前 Windows GUI 能实时显示距离、状态、频率和曲线，并保存 CSV。
- ROS 2 节点已在仓库中实现为 `project_link_vl53l0x`，并已接入视觉抓取影子模式和
  可选距离控制；仍需在 Orin 完成构建、udev、冷插拔、距离标定和实机抓取验收。

仓库资产：

~~~text
external/esp32_c3_vl53l0x_usb_bridge/
  CMakeLists.txt
  sdkconfig.defaults
  main/
    app_main.c
    vl53l0x.c
    vl53l0x.h
    Kconfig.projbuild
  host/
    vl53l0x_monitor.py
    requirements.txt
  README.md
~~~

## 2. 接线

默认接线：

| VL53L0X 转接板 | ESP32-C3 | 说明 |
| --- | --- | --- |
| VIN / VCC | 3V3 | 当前推荐接法，避免 I²C 被未知模块上拉到 5V |
| GND | GND | 必须共地 |
| SDA | GPIO4 | 固件可通过 menuconfig 修改 |
| SCL | GPIO5 | 固件可通过 menuconfig 修改 |
| GPIO1 / INT | 不接 | 当前固件轮询测距，不使用中断 |
| XSHUT / SHDN | 见下文 | 只有模块已有板载上拉时才能不接 |

XSHUT 是输入脚，不应在没有上拉时悬空。常见 VL53L0X 成品转接板带上拉，
此时可以不接；若板上没有上拉，使用约 10 kΩ 电阻上拉到 3.3V。

如果以后要消除“只复位 ESP32、但 VL53L0X 没有复位”的状态差异，建议把
XSHUT 接到一个空闲 ESP32-C3 GPIO，并由固件在启动时执行低电平到高电平的
硬复位。未完成该改动前，遇到 SENSOR_INIT / ESP_ERR_INVALID_RESPONSE 时，
应让 ESP32-C3 和 VL53L0X 一起断电重启，而不是只按 ESP32 Reset。

## 3. USB 串口协议

固件输出是 CRLF 结尾的 UTF-8/ASCII 文本流。ROS 节点只应接受 DATA 行，
忽略 ESP-IDF 日志、注释和未知行。

启动说明：

~~~text
# VL53L0X_USB_BRIDGE,1
# DATA,sequence,time_ms,distance_mm,range_status
~~~

有效测距：

~~~text
DATA,42,2150,687,0
~~~

字段：

| 字段 | 含义 |
| --- | --- |
| DATA | 固定帧类型 |
| sequence | ESP32 启动后的递增序号 |
| time_ms | ESP32 启动后的单调毫秒时间，不是 ROS 时间 |
| distance_mm | 毫米距离 |
| range_status | 0 表示有效；非 0 不得用于控制 |

错误行：

~~~text
ERROR,0,I2C_SETUP,ESP_ERR_NOT_FOUND
ERROR,1234,RANGE_READ,ESP_ERR_TIMEOUT
~~~

解析边界：

- DATA 必须恰好有 5 个逗号分隔字段。
- sequence、time_ms、distance_mm、range_status 必须能解析为非负整数。
- range_status 非 0 时不发布有效 Range。
- ROS 消息时间戳使用主机收到数据时的 ROS clock；不要把 time_ms 直接转换成
  Unix 或 ROS 时间。
- 串口单行读取必须设置最大长度，例如 256 bytes，避免异常日志造成无界缓存。
- sequence 回退通常意味着 ESP32 重启，应记录状态，但允许新的序列从 0 开始。

## 4. Windows 上位机

安装和启动：

~~~powershell
cd 'C:\Users\XWen1024\Documents\ROS2小车\external\esp32_c3_vl53l0x_usb_bridge\host'
py -m pip install -r requirements.txt
py vl53l0x_monitor.py --port COM21 --autoconnect
~~~

Windows GUI 是台架测试工具，当前直接独占 COM 口。它适合：

- 验证接线和量程；
- 观察短时曲线；
- 保存 CSV；
- 在 ROS 2 部署前确认固件输出。

它不能与 ROS 串口节点同时运行。一个串口同一时间只能有一个 owner。

## 5. ROS 2 推荐数据流

推荐架构：

~~~text
VL53L0X
-> I2C
-> ESP32-C3 firmware
-> USB CDC / USB Serial-JTAG text stream
-> project_link_vl53l0x serial_range_node
-> /vl53l0x/range          sensor_msgs/msg/Range
-> /vl53l0x/status         std_msgs/msg/String JSON
-> RViz / rosbag2 / grasp controller / optional Nav2 RangeSensorLayer
~~~

关键所有权规则：

- ROS 2 节点是 Linux/Orin 上唯一的串口 owner。
- Windows GUI 与 ROS 2 节点不得同时打开同一块 ESP32。
- 节点只发布感知数据，绝不发布 cmd_vel。
- Point-LIO、scan_accumulated 和现有 Nav2 障碍层仍是移动底盘的主感知链。
- 单个 VL53L0X 不是安全激光扫描器，不能替代急停、碰撞条、2D LiDAR 或人员
  监督。

## 6. ROS 2 接口定义

推荐标准话题：

~~~text
/vl53l0x/range
  sensor_msgs/msg/Range

/vl53l0x/status
  std_msgs/msg/String
~~~

Range 字段映射：

| Range 字段 | 建议值 |
| --- | --- |
| header.stamp | Orin 接收帧时的 ROS clock |
| header.frame_id | 参数化，例如 vl53l0x_front_link |
| radiation_type | Range.INFRARED |
| field_of_view | 0.4363 rad，约 25 度；最终按模块资料校正 |
| min_range | 0.03 m |
| max_range | 2.0 m，现场可保守降低 |
| range | distance_mm / 1000.0 |

建议状态 JSON：

~~~json
{"state":"reading","reason":"valid","accepted":1234,"rejected":2,
 "last_sequence":1235,"last_sensor_time_ms":62500}
~~~

非 0 range_status、超出配置量程、格式错误和串口异常只更新 status，不发布
新的有效 Range。

## 7. ROS 2 Python 包

仓库已新增：

~~~text
src/project_link_vl53l0x/
  package.xml
  setup.py
  setup.cfg
  resource/project_link_vl53l0x
  project_link_vl53l0x/
    __init__.py
    serial_range_node.py
  config/vl53l0x_front.yaml
  launch/vl53l0x.launch.py
  test/
    test_protocol.py
~~~

package.xml 至少声明：

~~~xml
<buildtool_depend>ament_python</buildtool_depend>
<depend>rclpy</depend>
<depend>sensor_msgs</depend>
<depend>std_msgs</depend>
<exec_depend>python3-serial</exec_depend>
<test_depend>python3-pytest</test_depend>
~~~

setup.py 入口：

~~~python
entry_points={
    "console_scripts": [
        "serial_range_node = project_link_vl53l0x.serial_range_node:main",
    ],
}
~~~

建议节点参数：

| 参数 | 默认建议 |
| --- | --- |
| device | /dev/vl53l0x-front |
| baudrate | 115200 |
| frame_id | vl53l0x_front_link |
| range_topic | /vl53l0x/range |
| status_topic | /vl53l0x/status |
| min_range_m | 0.03 |
| max_range_m | 2.0 |
| field_of_view_rad | 0.4363 |
| serial_timeout_sec | 0.20 |
| reconnect_interval_sec | 2.0 |
| stale_timeout_sec | 0.50 |
| max_line_bytes | 256 |

参考节点主体：

~~~python
#!/usr/bin/env python3
from __future__ import annotations

import json
import threading

import rclpy
from rclpy.node import Node
from rclpy.qos import (
    DurabilityPolicy,
    HistoryPolicy,
    QoSProfile,
    ReliabilityPolicy,
)
from sensor_msgs.msg import Range
from std_msgs.msg import String


class Vl53l0xSerialRangeNode(Node):
    def __init__(self) -> None:
        super().__init__("vl53l0x_serial_range_node")
        self.declare_parameter("device", "/dev/vl53l0x-front")
        self.declare_parameter("baudrate", 115200)
        self.declare_parameter("frame_id", "vl53l0x_front_link")
        self.declare_parameter("range_topic", "/vl53l0x/range")
        self.declare_parameter("status_topic", "/vl53l0x/status")
        self.declare_parameter("min_range_m", 0.03)
        self.declare_parameter("max_range_m", 2.0)
        self.declare_parameter("field_of_view_rad", 0.4363)
        self.declare_parameter("serial_timeout_sec", 0.20)
        self.declare_parameter("reconnect_interval_sec", 2.0)
        self.declare_parameter("max_line_bytes", 256)

        sensor_qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=5,
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE,
        )
        self._range_pub = self.create_publisher(
            Range, str(self.get_parameter("range_topic").value), sensor_qos
        )
        self._status_pub = self.create_publisher(
            String, str(self.get_parameter("status_topic").value), 10
        )
        self._stop = threading.Event()
        self._accepted = 0
        self._rejected = 0
        self._last_sequence = None
        self._thread = threading.Thread(
            target=self._serial_loop,
            name="vl53l0x-serial-reader",
            daemon=True,
        )
        self._thread.start()

    def _publish_status(self, state: str, reason: str) -> None:
        message = String()
        message.data = json.dumps(
            {
                "state": state,
                "reason": reason,
                "accepted": self._accepted,
                "rejected": self._rejected,
                "last_sequence": self._last_sequence,
            },
            separators=(",", ":"),
        )
        self._status_pub.publish(message)

    def _handle_line(self, line: str) -> None:
        if not line.startswith("DATA,"):
            return
        fields = line.split(",")
        if len(fields) != 5:
            self._rejected += 1
            self._publish_status("reading", "bad_field_count")
            return
        try:
            sequence = int(fields[1])
            sensor_time_ms = int(fields[2])
            distance_mm = int(fields[3])
            range_status = int(fields[4])
        except ValueError:
            self._rejected += 1
            self._publish_status("reading", "bad_integer")
            return

        if min(sequence, sensor_time_ms, distance_mm, range_status) < 0:
            self._rejected += 1
            self._publish_status("reading", "negative_field")
            return

        if self._last_sequence is not None and sequence < self._last_sequence:
            self._publish_status("reading", "device_restarted")
        self._last_sequence = sequence

        min_range = float(self.get_parameter("min_range_m").value)
        max_range = float(self.get_parameter("max_range_m").value)
        distance_m = distance_mm / 1000.0
        if range_status != 0:
            self._rejected += 1
            self._publish_status("reading", f"sensor_status_{range_status}")
            return
        if not min_range <= distance_m <= max_range:
            self._rejected += 1
            self._publish_status("reading", "outside_configured_range")
            return

        message = Range()
        message.header.stamp = self.get_clock().now().to_msg()
        message.header.frame_id = str(
            self.get_parameter("frame_id").value
        ).lstrip("/")
        message.radiation_type = Range.INFRARED
        message.field_of_view = float(
            self.get_parameter("field_of_view_rad").value
        )
        message.min_range = min_range
        message.max_range = max_range
        message.range = distance_m
        self._range_pub.publish(message)
        self._accepted += 1
        self._publish_status("reading", "valid")

    def _serial_loop(self) -> None:
        try:
            import serial
        except ImportError as exc:
            self.get_logger().error(f"pyserial missing: {exc}")
            self._publish_status("fault", "pyserial_missing")
            return

        device = str(self.get_parameter("device").value)
        baudrate = int(self.get_parameter("baudrate").value)
        timeout = float(self.get_parameter("serial_timeout_sec").value)
        reconnect = float(self.get_parameter("reconnect_interval_sec").value)
        max_line = int(self.get_parameter("max_line_bytes").value)

        while rclpy.ok() and not self._stop.is_set():
            try:
                with serial.Serial(
                    device,
                    baudrate,
                    timeout=timeout,
                    xonxoff=False,
                    rtscts=False,
                    dsrdtr=False,
                ) as port:
                    port.dtr = False
                    port.rts = False
                    self.get_logger().info(
                        f"Reading VL53L0X bridge from {device} at {baudrate} 8N1"
                    )
                    self._publish_status("reading", "connected")
                    while rclpy.ok() and not self._stop.is_set():
                        raw = port.read_until(b"\n", max_line)
                        if not raw:
                            continue
                        line = raw.decode("utf-8", errors="replace").strip()
                        self._handle_line(line)
            except Exception as exc:
                self.get_logger().error(f"VL53L0X serial fault: {exc}")
                self._publish_status("fault", "serial_disconnected")
                self._stop.wait(reconnect)

    def destroy_node(self):
        self._stop.set()
        self._thread.join(timeout=2.0)
        return super().destroy_node()


def main() -> None:
    rclpy.init()
    node = Vl53l0xSerialRangeNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
~~~

正式实现时应把协议解析提取成纯 Python 函数并增加单元测试，不要只依赖实机。

## 8. Orin 稳定设备名

不要在 launch/config 中写死 ttyACM0。先连接 ESP32-C3：

~~~bash
ls -l /dev/ttyACM*
udevadm info --query=property --name=/dev/ttyACM0
udevadm info --attribute-walk --name=/dev/ttyACM0
~~~

确认以下字段：

~~~text
idVendor  = 303a
idProduct = 1001
serial    = 设备实际序列值
~~~

在 Orin 本地创建规则，不把私人设备序列号提交到 Git：

~~~udev
SUBSYSTEM=="tty", ATTRS{idVendor}=="303a", ATTRS{idProduct}=="1001", ATTRS{serial}=="<verified-serial>", SYMLINK+="vl53l0x-front", GROUP="dialout", MODE="0660"
~~~

部署：

~~~bash
sudo install -m 0644 /path/to/local-rule \
  /etc/udev/rules.d/99-project-link-vl53l0x.rules
sudo udevadm control --reload-rules
sudo udevadm trigger
ls -l /dev/vl53l0x-front
~~~

用户必须属于 dialout：

~~~bash
sudo usermod -aG dialout wte
~~~

重新登录后生效。若机器人上还有其他 ESP32-C3，必须匹配准确 serial 或稳定
物理 USB path，不能只用 303a:1001。

## 9. TF 与安装位置

先确定用途，不要混用：

### 9.1 底盘前向测距

- 话题建议：/vl53l0x/front/range
- frame：vl53l0x_front_link
- parent：base_link
- 可在验证后考虑接入 Nav2 local costmap。

### 9.2 SO-101 末端测距

- 话题建议：/visual_grasp/tof_range
- frame：按 SO-101 末端 link 命名。
- parent：真实末端/夹爪 link。
- 只用于抓取逼近和距离闭环，不接 Nav2 costmap。

底盘安装示例：

~~~xml
<link name="vl53l0x_front_link"/>
<joint name="base_to_vl53l0x_front" type="fixed">
  <parent link="base_link"/>
  <child link="vl53l0x_front_link"/>
  <origin xyz="MEASURE_X MEASURE_Y MEASURE_Z"
          rpy="MEASURE_ROLL MEASURE_PITCH MEASURE_YAW"/>
</joint>
~~~

Project LINK 的传感器 TF 单一权威是
src/turn_on_wheeltec_robot/urdf/patrol_robot.urdf.xacro。固定安装后应在该
xacro 中加入真实测量值，不要再启动一个重复 static_transform_publisher。

## 10. 参数文件、构建和启动

参数文件示例：

~~~yaml
vl53l0x_serial_range_node:
  ros__parameters:
    device: /dev/vl53l0x-front
    baudrate: 115200
    frame_id: vl53l0x_front_link
    range_topic: /vl53l0x/front/range
    status_topic: /vl53l0x/status
    min_range_m: 0.03
    max_range_m: 2.0
    field_of_view_rad: 0.4363
    serial_timeout_sec: 0.20
    reconnect_interval_sec: 2.0
    max_line_bytes: 256
~~~

Orin 依赖和构建：

~~~bash
cd /home/wte/wheeltec_robot
source /opt/ros/humble/setup.bash
sudo apt install python3-serial
colcon build --packages-select project_link_vl53l0x
source install/setup.bash
~~~

当前 Orin 的 Python/setuptools 环境对部分 ament_python 包的
--symlink-install 不兼容。第一版沿用 UWB 包的做法，使用普通安装构建。

启动：

~~~bash
source /home/wte/wheeltec_robot/scripts/project_link_env.sh
ros2 run project_link_vl53l0x serial_range_node \
  --ros-args \
  --params-file /home/wte/wheeltec_robot/src/project_link_vl53l0x/config/vl53l0x_front.yaml
~~~

## 11. ROS 2 验收

只读验证：

~~~bash
ros2 node list | grep vl53l0x
ros2 topic info -v /vl53l0x/front/range
ros2 topic echo --once /vl53l0x/front/range
ros2 topic hz /vl53l0x/front/range
ros2 topic echo /vl53l0x/status
~~~

预期：

- frame_id 与 URDF 中 link 完全一致；
- status 为 reading/valid；
- 静止目标距离与卷尺误差满足使用需求；
- 话题频率接近固件输出频率，无持续大幅抖动；
- 拔掉 USB 后 status 在有限时间内进入 fault；
- 重新插入后节点自动重连；
- 节点不出现在 cmd_vel 发布者列表中。

记录：

~~~bash
ros2 bag record \
  /vl53l0x/front/range \
  /vl53l0x/status \
  /tf \
  /tf_static
~~~

## 12. 可选 Nav2 接入

只有底盘固定前向安装、TF 正确、距离实测稳定后，才考虑把 Range 加到 local
costmap。现有 scan_accumulated 障碍层必须保留。

Humble 常用配置方向：

~~~yaml
local_costmap:
  local_costmap:
    ros__parameters:
      plugins: ["obstacle_layer", "range_layer", "inflation_layer"]
      range_layer:
        plugin: "nav2_costmap_2d::RangeSensorLayer"
        topics: ["/vl53l0x/front/range"]
        input_sensor_type: VARIABLE
        clear_threshold: 0.20
        mark_threshold: 0.80
        clear_on_max_reading: true
        no_readings_timeout: 0.50
~~~

上面只是接入模板，不是已验证的本车参数。启用前必须确认 Orin 安装的 Humble
插件参数名，并在 RViz 中检查标记锥体、障碍位置和清除行为。

安全边界：

- 第一阶段只在 local costmap 使用，不加入 global costmap。
- 不因为 VL53L0X 单次读数直接发布 cmd_vel 或绕过 Nav2。
- 数据过期时停止发布新 Range，并报告 fault；不得重复发布旧距离。
- 不把短距 ToF 当成后向防撞依据。
- 若安装在机械臂末端，禁止接入 Nav2 costmap。

## 13. 当前已知问题

1. ESP32-C3 Reset 不一定同时复位由 3V3 供电的 VL53L0X。曾观察到有效测距后
   连续只复位 MCU，第二次初始化返回 ESP_ERR_INVALID_RESPONSE。
2. 当前恢复方式是让 ESP32-C3 与 VL53L0X 一起断电重启。
3. 推荐后续把 XSHUT 接到 GPIO，固件启动时执行硬复位，并加入自动初始化重试。
4. 当前固件在初始 I²C/传感器初始化失败后会从 app_main 返回，不会自动重试；
   ROS 节点的串口重连不能修复这个设备端状态。
5. Windows COM21 只是在本机当前枚举结果，不能写入 Orin 配置。
6. 当前 GUI 直接读串口，尚无 ROS topic subscriber 模式。

## 14. 下一位实现者的顺序

1. 按 SO-101 末端用途安装传感器，确认光轴不照到夹爪手指，并固定 XSHUT 接法。
2. 在 Orin 普通安装构建 `project_link_vl53l0x`，安装 `python3-serial`。
3. 使用实际设备序列建立 `/dev/vl53l0x-gripper` 私有 udev 别名并冷插拔验证。
4. 先独立验证 `/visual_grasp/tof_range`、status、频率、拔线重连和 rosbag2。
5. 使用 `start_visual_grasp_tmux.sh --with-tof` 启动影子模式，保持
   `tof_control_enabled=false`。
6. 用卷尺和夹爪接触位置标定 `tof_grasp_distance_m`，至少记录 20 帧稳定样本。
7. 完成至少 10 次 `WOULD_GRASP` 影子判断后，才启用 ToF 控制。
8. 验证 stale、拔线、遮挡和非法数据均保持机械臂停止。
9. 完成连续 3 次监督低速距离闭环抓取。
10. 不接 Nav2 costmap，也不为移动末端伪造底盘固定 TF。

## 15. 完成交接门槛

- [x] ESP32-C3 固件编译和烧录通过。
- [x] 原生 USB 控制台通过。
- [x] VL53L0X 有效 43 mm 实测帧通过。
- [x] Windows GUI 实时显示和 CSV 通过。
- [ ] XSHUT/设备端自动恢复完成。
- [x] ROS 2 package 实现并加入协议测试。
- [x] 视觉抓取影子模式、距离控制模式和 GUI 状态接口已实现。
- [ ] Orin udev 稳定别名通过冷插拔验证。
- [ ] sensor_msgs/Range 频率、TF 和 rosbag2 通过。
- [ ] 完成影子模式标定和连续 3 次监督低速抓取。
