# Project LINK Voice

本目录是当前分支的 ROS 2 语音服务包。它与 S2S 分支完全独立；本分支不要引用、合并或依赖 S2S 分支的运行时代码。

当前主链路：

```text
讯飞串口唤醒
-> USB 麦克风 16 kHz 单声道 PCM
-> FunASR FunVAD 端点检测
-> 火山双向流式 ASR（可切换本地 faster-whisper）
-> DeepSeek 官方 API / deepseek-v4-flash / thinking disabled
-> 流式 Tool Calling
-> Python 白名单与安全确认
-> 火山双向流式 TTS 首包播放
-> Nav2 或受限演示动作
```

默认交互是有界连续对话：一次串口唤醒后只在首轮播放“我在，请说”，每次回答播放完成后自动重新打开麦克风；连续静音 `8` 秒、达到 `20` 轮或会话持续 `300` 秒会固定播报“好的，我退下了”并回到等待唤醒。说“停止、取消、退出、退下、休息、不用了、算了、再见、拜拜”等本地关键词会绕过 LLM，立即取消当前底盘/机械臂/演示动作，播放同一句固定回复并结束会话。

## 运行环境

- 目标设备：Jetson Orin Nano，Ubuntu 22.04，ROS 2 Humble。
- 工作区：`/home/wte/wheeltec_robot`。
- ROS 网络：`ROS_DOMAIN_ID=42`、`ROS_LOCALHOST_ONLY=0`，由 `scripts/project_link_env.sh` 设置。
- Python：ROS 2 Humble 的 Python 3.10；语音依赖使用工作区 `.venv-voice`，创建时必须带 `--system-site-packages`，否则虚拟环境看不到 `rclpy`。
- GPU：FunVAD 和本地 Whisper 默认使用 CUDA；安装 PyTorch 时必须选择与 JetPack 匹配的版本，不能直接用通用 pip wheel 覆盖。
- 音频与串口：PyAudio/PortAudio、ALSA、`pyserial`；讯飞板串口默认自动扫描，麦克风索引 `-1` 表示使用 PyAudio 默认输入设备。
- 云端：火山 ASR、火山 TTS、DeepSeek LLM 使用三套独立凭据。TTS App ID/Token 不保证拥有 ASR 权限。

首次安装：

```bash
cd /home/wte/wheeltec_robot
source /opt/ros/humble/setup.bash
python3 -m venv --system-site-packages .venv-voice
source .venv-voice/bin/activate
# 先安装与当前 JetPack/CUDA 匹配的 PyTorch。
pip install -r src/project_link_voice/requirements-orin.txt
colcon build --symlink-install --packages-select project_link_voice_interfaces project_link_voice
```

每次运行前建议执行。现有 Orin 如果没有 `.venv-voice`，直接使用已经安装好依赖的系统 Python；不要因为目录不存在而阻断启动：

```bash
cd /home/wte/wheeltec_robot
if [ -f .venv-voice/bin/activate ]; then source .venv-voice/bin/activate; fi
source scripts/project_link_env.sh
source /home/wte/.config/project_link/voice_api.env
source scripts/project_link_voice_io.sh
```

首次部署或更新设备规则后运行一次：

```bash
cd /home/wte/wheeltec_robot
bash scripts/install_project_link_voice_io_aliases.sh
```

它将讯飞唤醒串口
`/dev/serial/by-id/usb-WCH.CN_USB_Single_Serial_0004-if00` 固定为
`/dev/project_link_wakeup`。麦克风按 `XFM-DP-V0.0.18` 名称匹配，不使用会随重插变化的 PyAudio 索引；喇叭固定为 Pulse sink
`alsa_output.usb-C-Media_Electronics_Inc._USB_Audio_Device-00.analog-stereo`。
每次启动还会把 Pulse 默认录音源切到名称以
`alsa_input.usb-iflytek_XFM-DP-V0.0.18_` 开头的讯飞 source，防止板载输入或
USB 喇叭 monitor 被误选为麦克风。

代码更新后如果仍在运行旧行为，通常是没有重新构建或没有重新 `source install/setup.bash`。`scripts/project_link_env.sh` 会加载当前工作区的 `install/setup.bash`。

## API 配置

私密配置只放在 Orin，不提交 Git：

```bash
mkdir -p /home/wte/.config/project_link
cp src/project_link_voice/config/voice_api.env.example /home/wte/.config/project_link/voice_api.env
chmod 600 /home/wte/.config/project_link/voice_api.env
nano /home/wte/.config/project_link/voice_api.env
```

生产配置：

```bash
export PROJECT_LINK_ASR_PROVIDER=volcano
export VOLCANO_ASR_API_KEY=你的火山ASR_API_KEY
export VOLCANO_ASR_RESOURCE_ID=volc.seedasr.sauc.duration
export VOLCANO_ASR_ENDPOINT=wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async

export DEEPSEEK_API_KEY=你的DeepSeek官方API_KEY

export VOLCANO_APP_ID=你的火山TTS_APP_ID
export VOLCANO_ACCESS_TOKEN=你的火山TTS_ACCESS_TOKEN
export VOLCANO_RESOURCE_ID=seed-tts-2.0
export VOLCANO_SPEAKER=你的音色ID
```

本地 ASR 备选：

```bash
export PROJECT_LINK_ASR_PROVIDER=faster_whisper
export PROJECT_LINK_WHISPER_MODEL=/home/wte/.cache/project_link/models/faster-whisper-small
```

当前实现不会在火山 ASR 失败时自动切到 Whisper，防止现场故障被静默掩盖。切换 Provider 后必须重启节点。

## 启动前检查

扫描讯飞串口、麦克风、扬声器和环境变量：

```bash
cd /home/wte/wheeltec_robot
if [ -f .venv-voice/bin/activate ]; then source .venv-voice/bin/activate; fi
source scripts/project_link_env.sh
source /home/wte/.config/project_link/voice_api.env
source scripts/project_link_voice_io.sh
python3 scripts/scan_voice_demo_io.py --require-asr
```

常见问题：

- 火山 ASR 返回 `403`：ASR Key/资源未授权，不要拿旧 TTS Token 代替专用 ASR Key。
- 唤醒成功但“没有听到有效语音”：优先核对 PyAudio 输入索引、ALSA 默认输入、采样率和 USB 麦克风是否被其他进程独占。
- 有文字但没有声音：核对 `VOLCANO_SPEAKER`、ALSA 默认输出、pygame mixer 和 USB 喇叭音量。
- 找不到 `rclpy`：重建带 `--system-site-packages` 的 `.venv-voice`，并先 source ROS 2。
- 找不到 ROS 包或行为没更新：重新 `colcon build`，再 source `scripts/project_link_env.sh`。
- 没有 `[VOICE_TIMING]`：确认 YAML 中 `timing_debug_enabled: true` 和 `timing_console_enabled: true`。
- 回答后没有继续监听：确认 `continuous_conversation_enabled: true`，并检查控制台是否出现 `Continuous conversation started`。
- 连续会话过早或过晚退出：调整 `continuous_silence_timeout_sec`；首轮唤醒后的无人说话仍使用独立的 `audio_no_speech_timeout_sec`。

## 控制台启动命令

下面是推荐的**纯语音安全测试**命令：保留真实唤醒、VAD、ASR、DeepSeek、Tool Calling 和 TTS，但禁止 Nav2 运动与机械臂，并把完整控制台保存到带日期的日志文件。

```bash
cd /home/wte/wheeltec_robot && \
if [ -f .venv-voice/bin/activate ]; then source .venv-voice/bin/activate; fi && \
source scripts/project_link_env.sh && \
source /home/wte/.config/project_link/voice_api.env && \
source scripts/project_link_voice_io.sh && \
stdbuf -oL -eL ros2 launch project_link_voice voice_nav2.launch.py \
  enable_motion:=false \
  enable_visual_grasp:=false \
  pure_test_mode:=on \
  wakeup_serial_port:=/dev/serial/by-id/usb-WCH.CN_USB_Single_Serial_0004-if00 \
  audio_input_device_index:=-1 \
  audio_input_device_name:=XFM-DP-V0.0.18 \
  2>&1 | tee "$HOME/voice_console_$(date +%Y%m%d_%H%M%S).log"
```

要测试 Nav2 时，先单独确认 Navigation Two 已健康，再使用仓库脚本；默认仍不允许语音发送运动 Goal：

```bash
cd /home/wte/wheeltec_robot
./scripts/start_voice_nav2_stack.sh --restart --attach
```

只有现场清空、Nav2/TF/代价地图正常且有人握住物理 E-stop 时，才可显式加入 `--enable-motion`。

独立短距离 `/cmd_vel` 演示使用：

```bash
./scripts/start_llm_voice_car_demo.sh --restart
```

该演示模式不使用 SLAM 或避障，只允许受限的前进、后退、左转、右转、转圈和停止，不得当作生产导航。

## 控制台时间线

每条真实阶段日志使用同一 `trace_id`，格式为：

```text
[VOICE_TIMING] 2026-08-13T14:23:45.123+08:00 +183.600ms total=912.400ms trace=4f50c281db13 phase=llm_first_text phase_elapsed=183.412ms
```

- 时间戳：该阶段被记录时的本地绝对时间，精确到毫秒并带时区。
- `+183.600ms`：距离同一轮语音交互的上一个真实阶段过去多久。
- `total=912.400ms`：从本轮 `trace` 创建到当前阶段的累计时间。
- `phase_elapsed=183.412ms`：该阶段自己的耗时；对 `llm_first_text` 来说，是该次 LLM 请求发出到收到首个非空文本字符包的时间。
- `metric=derived`：这是跨阶段派生指标，不会推进“距离上一步”的时间基准。

重点阶段：

| phase | 含义 |
| --- | --- |
| `speech_end_to_vad` | 用户估计说完到 FunVAD 结束录音 |
| `vad_to_asr_final` | VAD 最后一包到 ASR 最终文本 |
| `asr_final_to_llm_send` | ASR 最终文本到 LLM 请求发出 |
| `llm_first_delta` | LLM 首个有效流式包；可能是文本，也可能是 Tool Call |
| `llm_first_text` | LLM 首个非空文本字符包，适合对比不同模型首字延迟 |
| `llm_tool_call_complete` | Function Calling 参数流接收完整 |
| `python_tool` | Python 参数校验和工具执行 |
| `tts_first_audio` | TTS 请求到首个音频包 |
| `tts_playback_started` | 播放器开始输出音频 |
| `speech_end_to_first_playback` | 用户说完到机器人真正开始发声的端到端耗时 |

JSONL 原始日志保存在：

```text
~/.ros/project_link_voice/voice_debug.jsonl
~/.ros/project_link_voice/voice_timing.jsonl
```

查看实时阶段：

```bash
tail -F ~/.ros/project_link_voice/voice_timing.jsonl
```

统计最近 20 次、包括 `llm_first_text` 在内的各阶段均值/P50/P95：

```bash
cd /home/wte/wheeltec_robot
python3 src/project_link_voice/tools/summarize_voice_timing.py \
  ~/.ros/project_link_voice/voice_timing.jsonl --last 20 --all-phases
```

做模型对比时固定 ASR、提示词、网络、TTS 音色和测试句，只改 `llm_model`，重点比较 `llm_first_delta`、`llm_first_text`、`llm_tool_call_complete` 和端到端 `speech_end_to_first_playback`。
