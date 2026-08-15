# Project LINK 语音链路延迟 A/B 测试方法

更新时间：2026-08-13
适用平台：Jetson Orin Nano / Ubuntu aarch64

## 1. 测试目的

本文用于公平比较两条语音链路：

```text
方案 S：Volcengine Embedded Kit WebSocket S2S
麦克风/PCM -> 云端 S2S -> Function Call -> 本地工具 -> 云端反馈 -> 扬声器

方案 L：当前解耦链路
麦克风 -> FunVAD + 火山双向流式 ASR -> DeepSeek -> 本地工具 -> 火山双向流式 TTS -> 扬声器
```

方案 S 所在 S2S 分支与当前解耦链路分支完全独立。A/B 只比较运行产物，
不得让两个分支在代码、依赖或启动脚本层互相引用。

最终回答三个问题：

1. 用户停止说话后，哪条链路更快开始播出有效回复？
2. 哪条链路的 Function Call 更快、更准、更稳定？
3. 延迟差异来自端点检测、ASR、LLM/FC、工具执行、TTS，还是本地音频缓冲？

不要只比较一次结果或平均值。每个测试条件至少运行 10 次，推荐 20 次，保留每次原始日志，并报告成功率、P50、P90、平均值和 min/max。

## 2. 两类测试必须分开

### 2.1 固定 PCM 软件基准

固定 PCM 能排除说话时长、音量、停顿和措辞变化，最适合判断 SDK、云端模型和反馈策略的变化。

当前 S2S smoke test 已支持直接发送固定 PCM。原解耦链路目前没有正式的“固定 PCM 注入整链路”入口，因此不能把 S2S 固定 PCM 的端到端数字和原链路的真实麦克风数字直接放在同一组统计中。

如需最高精度的同源比较，应后续增加一个仅用于测试的 PCM 输入入口，使原链路跳过声卡、但仍依次经过 FunVAD/ASR/LLM/工具/TTS。这个入口不得改动生产默认行为。

### 2.2 真实硬件体感 A/B

真实麦克风和扬声器测试用于比较用户实际体感。它包含声卡、音频分片、端点检测和播放缓冲等真实开销。

最公平的做法是：用同一个外部扬声器，在固定位置和音量播放同一个测试音频；两条链路使用同一个麦克风、扬声器和唤醒方式。另用手机或电脑连续录下“输入语音 + 机器人回复”，从波形测量：

```text
测试语音最后一个有效波形结束
->
机器人扬声器第一个有效回复波形开始
```

这是两条不同架构之间最可信的共同主指标。内部 logger 用来解释延迟构成，而不是替代声学端到端测量。

## 3. 公平性约束

每组 A/B 必须满足：

- 使用完全相同的指令文本；固定 PCM 测试记录 SHA256。
- 同一测试使用相同 Prompt、Tool schema、工具返回内容和成功反馈文案。
- 记录实际模型名称、模型版本、反馈策略和输入结束策略。
- 使用同一网络、同一 Jetson、同一声卡设备、同一麦克风距离和音量。
- 正式统计前每条链路预热 2 次；预热结果不计入统计。
- A/B 交替运行，例如 `S1 L1 L2 S2 S3 L3`，不要先跑完全部 S 再跑全部 L。
- 每次测试之间等待 3 至 5 秒，确保前一轮 response 和播放已经结束。
- 失败、超时、错误 Tool Call 和无最终音频都必须留下记录，不能从样本中静默删除。
- 延迟分布只统计成功样本；同时单独报告总尝试数和成功率。
- 不要同时运行两条语音识别链路，避免两个进程争用同一麦克风。
- 延迟测试一律关闭运动、Nav2、底盘和机械臂执行。

如果模型、Prompt、Tool schema 或反馈策略发生变化，应建立新测试组，不能继续追加到旧组。

## 4. 测试指令

至少包含以下两类：

### 4.1 普通短回答，不触发 Function Call

```text
请只回答“好”
```

目标回复尽量只有：

```text
好
```

该组用于测语音对话本体的延迟下限。

### 4.2 Function Calling

```text
请告诉我神奇数字。
```

工具固定为：

```text
get_magic_number -> {"number": 42}
```

该组用于测 FC 决策、工具返回和成功反馈。

当前固定 PCM：

```text
/home/wte/wheeltec_robot-volc-smoke/experiments/volc_s2s_smoke/assets/get_magic_number.pcm
格式：PCM S16LE / 16 kHz / mono
大小：86780 bytes
SHA256：c8e0c24793b68b0974de7a00beef3c585514c1c48206dc03f21baeb42c5ddee0
```

测试前仍应在设备上重新验证：

```bash
sha256sum /home/wte/wheeltec_robot-volc-smoke/experiments/volc_s2s_smoke/assets/get_magic_number.pcm
```

## 5. 统一时间点和指标

### 5.1 共同决策指标

| 指标 | 含义 |
|---|---|
| `speech_end_to_first_speaker_audio_ms` | 用户最后发声到扬声器开始有效回复，首要体感指标 |
| `speech_end_to_function_call_ms` | 用户最后发声到收到完整、可执行的 FC |
| `function_call_to_tool_done_ms` | 收到 FC 到本地工具完成 |
| `tool_done_to_first_speaker_audio_ms` | 工具完成到首次反馈音频 |
| `speech_end_to_response_done_ms` | 用户最后发声到整轮云端响应完成 |
| FC 正确率 | 是否调用了正确工具 |
| 参数正确率 | 参数能否通过本地 schema 校验 |
| 最终音频成功率 | 是否播出了正确且不重复的最终反馈 |
| 超时率 | 是否超过预定 response timeout |

跨架构做最终选择时，优先使用外部录音测得的 `speech_end_to_first_speaker_audio_ms`。内部时间点的定义不同，只用于拆解原因。

### 5.2 S2S 内部 logger

当前 logger 可观察的主要 phase 包括：

```text
volc_last_input_to_speech_stopped
volc_last_input_to_server_commit
volc_vad_stop_to_function_call
volc_last_input_to_function_call
volc_function_call_to_arguments_done
volc_local_function_execute
volc_function_call_to_output_sent
volc_function_output_to_response_created
volc_last_input_to_first_ai_audio
volc_function_output_to_first_ai_audio
volc_audio_callback_to_speaker_write
volc_last_input_to_speaker_write
volc_last_input_to_response_done
speaker_playback_drain
```

重点看：

```text
端点/提交：last input -> speech_stopped/commit
FC 决策：speech_stopped -> function_call
工具反馈：function output -> first AI audio
本地播放：audio callback -> speaker write
总体：last input -> speaker write
```

S2S 是黑盒时，不能把 `speech_stopped -> function_call` 擅自拆成 ASR 和 LLM 两段。没有官方事件就填写 `N/A`。

`last input audio sent` 是最后一个音频分片发送时间，不等于物理用户最后一个音素结束时间。其误差至少受采集分片大小、声卡缓冲和网络发送调度影响。

### 5.3 原解耦链路内部 logger

主要 phase 包括：

```text
wakeup_ack_playback
vad_record
speech_end_to_vad
asr_session_ready
asr_last_packet_sent
asr_final
vad_to_asr_final
asr_final_to_llm_send
llm_first_delta
llm_to_tool_call
llm_api_roundtrip
llm_tool_arguments_parse
python_tool
llm_total
tts_dispatch
tts_request_sent
tts_first_audio
tts_playback_started
tts_to_first_audio
first_audio_to_playback
speech_end_to_first_playback
tts_synthesis_complete
```

解释：

```text
vad_record              包含实际讲话和 FunVAD 尾部等待，不是纯 VAD 算法耗时
speech_end_to_vad       模型估计的最后语音结束到 FunVAD 正式判停
vad_to_asr_final        FunVAD 判停到火山 ASR 最终包
asr_final_to_llm_send   ASR 最终包到 DeepSeek 请求发出
llm_to_tool_call        DeepSeek 请求到完整 Function Call
llm_api_roundtrip       一次 DeepSeek 请求往返
llm_total               LLM 请求、Tool Call 解析和可能的后续请求总耗时
python_tool             本地 Python 工具执行耗时
tts_to_first_audio      TTS 请求发出到收到第一段 PCM
first_audio_to_playback 收到第一段 PCM 到 pygame 实际开始播放
speech_end_to_first_playback 模型估计语音结束到正式回答开始播放
tts_synthesis_complete  TTS 完整合成结束
```

不要把 phase 简单相加后冒充外部声学端到端延迟：ASR 在录音期间已并行发送，
LLM/TTS 也存在流式重叠。内部首选 `speech_end_to_first_playback`，最终用户体感仍以
外部录音的 `speech_end_to_first_speaker_audio_ms` 为准。

## 6. 建立测试目录并保存环境

在 Orin 上执行：

```bash
STAMP=$(date +%Y%m%d_%H%M%S)
AB_ROOT="$HOME/voice_latency_ab/$STAMP"
mkdir -p "$AB_ROOT"/{s2s,decoupled,acoustic}

{
  echo "test_started_at=$(date --iso-8601=seconds)"
  echo "host=$(hostname)"
  echo "kernel=$(uname -a)"
  echo "arch=$(uname -m)"
  echo "s2s_git=$(git -C /home/wte/wheeltec_robot-volc-voice rev-parse HEAD 2>/dev/null || true)"
  echo "smoke_git=$(git -C /home/wte/wheeltec_robot-volc-smoke rev-parse HEAD 2>/dev/null || true)"
  echo "legacy_git=$(git -C /home/wte/wheeltec_robot rev-parse HEAD 2>/dev/null || true)"
  echo "model=请手工填写实际后台模型"
  echo "prompt_version=请手工填写"
  echo "tool_schema_version=请手工填写"
  echo "feedback_strategy=请手工填写"
} > "$AB_ROOT/manifest.env"

cp "$HOME/.ros/project_link_voice/voice_timing.jsonl" \
  "$AB_ROOT/voice_timing_before.jsonl" 2>/dev/null || true
```

`manifest.env` 不得写 API key、Product Secret 或其他凭据。

推荐目录：

```text
~/voice_latency_ab/YYYYMMDD_HHMMSS/
├── manifest.env
├── s2s/
│   ├── run_01/
│   └── ...
├── decoupled/
│   ├── timing.jsonl
│   └── console.log
├── acoustic/
│   ├── continuous_recording.wav
│   └── acoustic_measurements.tsv
├── results.tsv
└── summary.tsv
```

这些是运行产物，不要提交到 Git。

## 7. S2S 固定 PCM 测法

下面是当前 FC + `input_tts` 反馈策略的单次命令：

```bash
cd /home/wte/wheeltec_robot-volc-smoke/experiments/volc_s2s_smoke

./scripts/run_smoke.sh \
  --artifact-dir artifacts/manual_test/run_01 \
  --pcm assets/get_magic_number.pcm \
  --expect-function-call \
  --feedback-strategy input-tts \
  --input-end server-vad \
  --response-timeout-sec 45
```

连续 10 次并隔离原始产物：

```bash
cd /home/wte/wheeltec_robot-volc-smoke/experiments/volc_s2s_smoke
OUT="$AB_ROOT/s2s"

for i in $(seq -w 1 10); do
  mkdir -p "$OUT/run_$i"
  ./scripts/run_smoke.sh \
    --artifact-dir "$OUT/run_$i" \
    --pcm assets/get_magic_number.pcm \
    --expect-function-call \
    --feedback-strategy input-tts \
    --input-end server-vad \
    --response-timeout-sec 45 \
    2>&1 | tee "$OUT/run_$i/console.log"
  sleep 4
done
```

每次检查并保留：

```text
smoke.log / console.log
response PCM/WAV
latency summary
Function Call 原始脱敏 JSON
是否正确调用工具
是否只播放一次最终反馈
```

如果改测 `function_call_output -> response.create -> 二次 LLM -> TTS`，必须建立另一测试组，并在 `manifest.env` 写明实际 `feedback_strategy`。不能把它和 `input_tts` 的样本合并，因为 `input_tts` 绕过了二次 LLM，语义和延迟都不同。

当前 smoke 目录已有批量与 commit A/B 工具时，也可以使用：

```text
scripts/run_latency_batch.sh
scripts/run_commit_ab.sh
```

先执行其 `--help`，把完整命令保存到该批次的 `manifest.env`，不要依赖以后可能变化的默认参数。

## 8. 原解耦链路安全启动

不要使用会启用底盘动作的延迟测试方式。推荐直接以 `enable_motion:=false` 启动：

```bash
cd /home/wte/wheeltec_robot
source scripts/project_link_env.sh
source /home/wte/.config/project_link/voice_api.env

ros2 launch project_link_voice voice_direct_drive.launch.py \
  enable_motion:=false \
  enable_audio:=true \
  enable_llm_tools:=true \
  enable_visual_grasp:=false \
  pure_test_mode:=on \
  wakeup_serial_port:=auto \
  audio_input_device_index:=-1 \
  2>&1 | tee "$AB_ROOT/decoupled/console.log"
```

确认日志显示运动未启用，并确认只有一个进程占用麦克风。不要同时启动 S2S live voice tmux 和原解耦语音节点。

测试结束后保存：

```bash
cp "$HOME/.ros/project_link_voice/voice_timing.jsonl" \
  "$AB_ROOT/decoupled/timing.jsonl"
cp "$HOME/.ros/project_link_voice/voice_debug.jsonl" \
  "$AB_ROOT/decoupled/debug.jsonl" 2>/dev/null || true
```

直接查看最近 20 次各阶段 P50/P95：

```bash
python3 src/project_link_voice/tools/summarize_voice_timing.py --last 20
```

### 8.1 文本级 LLM + Tool + TTS 测试

可以通过 topic 排除麦克风、VAD 和 ASR，只测旧链路的 LLM、Tool 和 TTS：

```bash
ros2 topic pub --once /voice/text_input std_msgs/msg/String \
  "data: '请告诉我神奇数字'"
```

这个结果只能回答“DeepSeek + 本地工具 + 火山 TTS 有多快”，不能标记为语音端到端延迟，也不能直接与 S2S 固定 PCM 的 `last input -> first audio` 比较。

## 9. 真实麦克风 A/B 执行步骤

1. 停止所有语音进程，确认麦克风未被占用。
2. 将外部播放设备固定在麦克风前同一位置，音量固定。
3. 启动外部连续录音，用于测声学端到端时间。
4. 启动方案 S，预热 2 次，然后完成 `S1`。
5. 完全停止方案 S，启动方案 L，预热后完成 `L1`。
6. 按 `S1 L1 L2 S2 S3 L3 ...` 交替，直到每条链路至少 10 个正式样本。
7. 每次播放同一个输入文件；唤醒方式、唤醒后等待时间保持一致。
8. 每轮口头或在纸面记录 run ID；听到错误/重复/无回复时立即标记，不重做覆盖。
9. 在外部录音波形中标记输入最后一个有效语音波形和机器人首个有效回复波形。
10. 将差值写入 `acoustic/acoustic_measurements.tsv`。

推荐 TSV：

```text
run_id	pipeline	case	model	feedback_strategy	input_hash	speech_end_to_first_audio_ms	fc_correct	args_correct	final_audio_ok	timeout	notes
S01	s2s	fc	deepseek-v4-flash	input_tts	c8e0...	1846	1	1	1	0
L01	decoupled	fc	deepseek-v4-flash	volcano_tts	c8e0...		1	1	1	0	待波形标注
```

不要使用“按下播放键”的时间作为语音结束点。播放文件本身有前后静音时，应以波形中最后一个有效语音为准。

## 10. 从 timing JSONL 查看每个 trace

现有日志使用 monotonic clock 计算 elapsed time，并用 `trace_id` 串联同一次交互。下面的标准库脚本不需要安装 `jq`，会列出每个 trace 的全部 phase：

```bash
python3 - "$HOME/.ros/project_link_voice/voice_timing.jsonl" <<'PY'
import json
import sys
from collections import defaultdict

traces = defaultdict(lambda: {"phases": {}, "summary": {}})
with open(sys.argv[1], encoding="utf-8") as f:
    for line in f:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        trace_id = row.get("trace_id")
        if not trace_id:
            continue
        if row.get("kind") == "timing" and row.get("phase"):
            traces[trace_id]["phases"][row["phase"]] = row.get("elapsed_ms")
        elif row.get("kind") == "timing_summary":
            traces[trace_id]["summary"] = row

for trace_id, data in traces.items():
    summary = data["summary"]
    print(f"\ntrace_id={trace_id} outcome={summary.get('outcome', 'N/A')} "
          f"total_ms={summary.get('total_ms', 'N/A')}")
    for phase, value in sorted(data["phases"].items()):
        print(f"  {phase:45s} {value}")
PY
```

注意：同一个 phase 可能在复杂流程中出现多次。正式统计时应先检查日志语义，不能无条件让后出现的值覆盖前一个值。`llm_api_roundtrip` 可能有第一次 FC 决策和工具后的第二次请求，应分别保留或使用 logger 中的请求序号字段。

## 11. 汇总统计方法

每个独立测试组至少输出：

```text
attempts
successes
success_rate
FC correctness
argument correctness
final audio success rate
timeout rate
P50
P90
mean
min
max
```

P50/P90 使用 nearest-rank：将 N 个成功延迟从小到大排列，百分位位置为：

```text
P50 = 第 ceil(0.50 * N) 个
P90 = 第 ceil(0.90 * N) 个
```

可将某一指标的成功样本保存为一列毫秒数，然后执行：

```bash
python3 - latency_ms.txt <<'PY'
import math
import statistics
import sys

values = []
with open(sys.argv[1], encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#"):
            values.append(float(line))

if not values:
    raise SystemExit("no successful latency samples")

values.sort()
rank = lambda p: values[math.ceil(p * len(values)) - 1]
print(f"N={len(values)}")
print(f"mean={statistics.fmean(values):.1f} ms")
print(f"P50={rank(0.50):.1f} ms")
print(f"P90={rank(0.90):.1f} ms")
print(f"min={values[0]:.1f} ms")
print(f"max={values[-1]:.1f} ms")
PY
```

对于失败样本，不应把 timeout 秒数混入成功延迟分布；应在成功率和 timeout 率中体现，并在原始表中保留。

## 12. 结果表模板

### 12.1 普通对话

| 链路/变体 | 尝试 | 成功率 | 用户结束到首音 P50 | P90 | 平均 | min/max | 备注 |
|---|---:|---:|---:|---:|---:|---:|---|
| S2S |  |  |  |  |  |  |  |
| 解耦链路 |  |  |  |  |  |  |  |

### 12.2 Function Calling

| 链路/变体 | 尝试 | FC 正确率 | 参数正确率 | 最终音频成功率 | 结束到 FC P50/P90 | 结束到首音 P50/P90 | 工具完成到首音 P50/P90 |
|---|---:|---:|---:|---:|---:|---:|---:|
| S2S + 二次 LLM |  |  |  |  |  |  |  |
| S2S + input_tts |  |  |  |  |  |  |  |
| 解耦链路 |  |  |  |  |  |  |  |

### 12.3 内部延迟拆分

| 链路 | Endpoint P50/P90 | ASR P50/P90 | FC/LLM P50/P90 | Tool P50/P90 | TTS 首包 P50/P90 | callback 到 speaker P50/P90 |
|---|---:|---:|---:|---:|---:|---:|
| S2S | 可观测事件或 N/A | N/A |  |  | 黑盒或可观测事件 |  |
| 解耦链路 |  |  |  |  |  | 当前无独立项则 N/A |

## 13. 成功和失败定义

### 普通对话成功

- 收到且播放了非空回复；
- 回复符合约束，例如只回答“好”；
- 没有重复播放；
- 在 timeout 内完成。

### FC 成功

- 调用工具名称正确；
- 参数 JSON 完整并通过 schema 校验；
- 本地工具实际返回成功；
- 服务端收到工具结果；
- 扬声器播放正确的最终反馈；
- 没有工具重复执行和回复重复播放。

以下均记为失败，而不是“慢样本”：

```text
无 Function Call
错误工具或错误参数
工具结果已返回但没有最终音频
超时
会话提前断开
回复重复
音频不可播放
```

## 14. 选择规则

建议按以下优先级选择：

1. 安全和正确性：错误工具、错误参数或重复执行不可接受。
2. 完整闭环成功率：不能只看成功样本有多快。
3. 真实声学端到端 P90：比单次最快值和平均值更接近长期体感。
4. FC P90 和 `tool done -> first audio` P90：用于定位是否值得绕过二次 LLM。
5. 可观测性、可维护性和故障隔离能力。

建议最低门槛：

```text
FC/参数正确率：正式决策批次应为 100%
最终音频成功率：至少 95%
超时率：不高于 5%
```

如果两条链路的声学端到端 P90 相差不到约 200 ms，或不到 10%，在只有 10 至 20 次样本时通常不应仅凭延迟决定；此时优先选择成功率更高、日志更完整、异常恢复更可靠的方案。

`input_tts` 或本地预生成 PCM 更快并不代表完整的“工具后二次 LLM”也同样快。报告中必须把这些策略作为独立变体命名。

## 15. 已知测量限制

- S2S 当前不暴露可独立验证的 ASR-final、LLM first token 和 TTS-start，不能伪造阶段延迟。
- 原解耦链路的 `vad_record` 同时包含用户讲话时长和端点等待。
- S2S 的 `last input sent` 和真实用户最后发声存在音频分片与声卡缓冲误差。
- 软件 `speaker_write` 早于声音真正从扬声器发出；声学测量会额外包含 ALSA/设备缓冲。
- 文本 topic 测试跳过 VAD 和 ASR，只能作为组件测试。
- 不同模型、Prompt、Tool schema、service tier 或反馈策略的结果不能混为同一总体。
- 10 次只够初筛；准备正式迁移时推荐至少 20 次，网络跨时段再复测一轮。

## 16. 最终报告结论格式

```text
测试日期：
Jetson / Git commit：
输入文件与 SHA256：
网络与音频设备：
S2S 模型 / Prompt / Tool / feedback strategy：
解耦链路 ASR / LLM / TTS 配置：

普通对话：
- S2S success / P50 / P90 / mean / min / max
- 解耦 success / P50 / P90 / mean / min / max

Function Calling：
- S2S FC 正确率、参数正确率、最终音频成功率、P50/P90
- 解耦链路 FC 正确率、参数正确率、最终音频成功率、P50/P90

最大延迟阶段：
失败模式：
选择：S2S / 解耦链路 / 暂不决策
选择理由：
原始数据目录：
```

只在两条链路使用相同测试边界、样本数和条件后做最终选择。内部 logger 负责说明“慢在哪里”，外部声学端到端测量负责说明“用户实际等了多久”。
