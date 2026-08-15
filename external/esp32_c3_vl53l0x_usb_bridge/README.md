# ESP32-C3 + VL53L0X USB 实时测距

这是一个独立的 ESP-IDF v6 工程。ESP32-C3 通过 I²C 读取 VL53L0X，
再向 ESP32-C3 内置 USB Serial/JTAG 控制台输出文本数据。默认配置适用于
本次检测到的原生 USB 板（VID:PID 为 303A:1001）。

默认输出格式：

    # VL53L0X_USB_BRIDGE,1
    # DATA,sequence,time_ms,distance_mm,range_status
    DATA,42,2150,687,0

只有 range_status 等于 0 才表示有效测距。非零状态仍会显示和保存，但不应
直接用于运动控制。

## 接线

固件默认使用 GPIO4 作为 SDA、GPIO5 作为 SCL。

| VL53L0X 转接板 | ESP32-C3 | 说明 |
| --- | --- | --- |
| VIN / VCC | 3V3 | 最稳妥的接法；不要在不清楚模块电路时直接接 5V |
| GND | GND | 必须共地 |
| SDA | GPIO4 | I²C 数据 |
| SCL | GPIO5 | I²C 时钟 |
| XSHUT / SHDN | 通常不接 | 仅当转接板自带上拉；否则用约 10 kΩ 上拉到 3.3V |
| GPIO1 / INT | 不接 | 本工程轮询数据，不使用中断 |

    VL53L0X                 ESP32-C3
    --------                --------
    VIN/VCC  -------------  3V3
    GND      -------------  GND
    SDA      -------------  GPIO4
    SCL      -------------  GPIO5
    XSHUT    -------------  NC（板载上拉存在时）
    GPIO1    -------------  NC

注意：

- 常见带稳压和电平转换的成品板可能声明 VIN 为 2.6 至 5.5V，但不同商家的
  板子并不一致。给模块接 3V3 可同时避免 SDA/SCL 被上拉到 5V。
- 如果板上没有 SDA/SCL 上拉电阻，分别增加一只 4.7 kΩ：
  SDA 到 3V3、SCL 到 3V3。ESP32-C3 内部弱上拉已开启，但不适合作为
  长线或高频 I²C 的唯一上拉。
- GPIO1/INT 是传感器输出，可以悬空。XSHUT 是输入，不能无条件悬空；
  只有确认转接板已有上拉时才可以不接。
- 线尽量短，首次测试建议低于 20 cm。

## 构建和烧录

在 PowerShell 中执行：

    Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
    . 'C:\Espressif\tools\Microsoft.v6.0.2.PowerShell_profile.ps1'
    cd 'C:\Users\XWen1024\Documents\ROS2小车\external\esp32_c3_vl53l0x_usb_bridge'
    idf.py set-target esp32c3
    idf.py build
    idf.py -p COM7 flash monitor

把 COM7 改成设备管理器里实际的端口。退出 monitor 使用 Ctrl+]。

如果激活脚本报告它找不到原先的 Python 3.12 可执行文件，说明 ESP-IDF 的
Python 虚拟环境已经失效。请先运行 Espressif Installation Manager 的
“Fix” 功能修复 v6.0.2（命令行工具是 eim fix，图形界面可运行 eim gui），
然后重新打开 PowerShell。不要手工修改虚拟环境中的 pyvenv.cfg 来伪装修复。

若要改 I²C 引脚或采样周期：

    idf.py menuconfig

进入 “VL53L0X USB bridge” 菜单。改完后重新执行 idf.py build flash。

如果以后换成只带 CP2102/CH340、没有连接 ESP32-C3 原生 USB 引脚的开发板，
还需要在 menuconfig 的 “Channel for console output” 中改用 UART0。

## Python 上位机

安装唯一的第三方依赖：

    cd 'C:\Users\XWen1024\Documents\ROS2小车\external\esp32_c3_vl53l0x_usb_bridge\host'
    py -m pip install -r requirements.txt
    py vl53l0x_monitor.py

上位机支持：

- 自动枚举 COM 口；
- 当前距离、状态和接收频率显示；
- 最近 300 个测量点的实时曲线；
- 将原始测量保存为带电脑时间戳的 CSV。

也可以指定端口并自动连接：

    py vl53l0x_monitor.py --port COM7 --autoconnect

不要同时运行 idf.py monitor 和 Python 上位机；Windows 上同一个 COM 口
通常只能被一个程序占用。

## 常见故障

- “VL53L0X not found at I2C address 0x29”：检查供电、共地、SDA/SCL 是否
  接反，以及 XSHUT 是否保持高电平。
- 能烧录但没有数据：开发板可能暴露了两个 COM 口。分别尝试 USB-UART
  桥和名为 “USB JTAG/serial debug unit” 的端口。
- 数值一直为非零状态：检查保护膜、目标反射率、环境强光和测量距离。
  VL53L0X 的可靠量程通常明显小于宣传的极限量程。
