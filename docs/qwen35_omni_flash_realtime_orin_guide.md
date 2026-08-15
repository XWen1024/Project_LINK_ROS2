# Qwen3.5-Omni-Flash-Realtime：Jetson Orin 工程接入精华手册

> 调研日期：2026-08-14
> 目标环境：NVIDIA Jetson Orin / Linux ARM64 / Python
> 目标场景：低延迟实时语音、可打断、本地 Function Calling、机器人/硬件控制
> 阅读方式：先看“诊断结论”，遇到问题再查对应章节。

---

## 0. 诊断结论

### 当前处方

**现在：继续使用 DashScope Python SDK + WebSocket。**

**架构上：把 DashScope 放在独立 Transport 层，业务层只认你自己的统一接口。**

**以后：只有出现以下情况，再切 Raw WebSocket：**

1. 主进程要从 Python 迁移到 C++ / Rust；
2. DashScope SDK 落后于最新 Realtime 协议，缺事件或缺参数；
3. 需要自己精确控制 ping/pong、重连、backpressure、队列、超时和状态机；
4. 需要极小依赖、单二进制部署或更严格的内存/线程控制；
5. SDK 本身真的在 Orin 上出现稳定性问题。

**不建议现在为了“更底层”而重写 Raw WS。** DashScope 的 `OmniRealtimeConversation` 本质上就是对同一条 Realtime WebSocket 协议的 Python 封装。官方 SDK 源码直接使用 `websocket.WebSocketApp` 连接你传入的 WSS 地址，并把 `session.update`、`input_audio_buffer.append` 等协议事件包装成 Python 方法。它不是字节 RTC 那类必须依赖厂商 ARM64 原生媒体库的 SDK。

### 对你当前系统最重要的三个动作

1. 模型从 Plus A/B 到 **`qwen3.5-omni-flash-realtime`**。官方在特定参考测试中，Flash 比 Plus 总响应快约 714ms，低延迟场景官方也建议优先 Flash。
2. VAD 参数全部显式填写，不吃 SDK 默认值：
   - `turn_detection_type="semantic_vad"`
   - `turn_detection_threshold=0.5`，Orin 风扇/环境噪声较大可试 0.55~0.65
   - `turn_detection_silence_duration_ms=1200` 起步
   - `prefix_padding_ms=300`
3. Function Calling 直接留在同一条 Realtime 会话里，本地执行 Orin 工具。不要再套普通 `MultiModalConversation.call()`。

---

## 1. 为什么此刻选 DashScope，而不是 Raw WebSocket

| 维度 | DashScope Python SDK | Raw WebSocket | 对你的结论 |
|---|---|---|---|
| 云端模型链路 | 同一 Realtime WSS | 同一 Realtime WSS | 基本相同 |
| 模型延迟 | 不会额外多一跳云服务 | 不会额外多一跳云服务 | Raw 不会神奇降低 TTFA |
| Orin ARM64 风险 | 低，且你已经实机跑通 | 低 | DashScope 已经有实证 |
| 厂商二进制库 | Realtime 层没有专有 RTC `.so` 依赖 | 无 | 都比 RTC SDK 轻 |
| 开发量 | 低 | 高 | 当前优先 DashScope |
| VAD / Tools / Audio API | 已封装 | 自己拼 JSON 事件 | DashScope 更省时间 |
| 最新协议字段 | 可能存在 SDK 跟进延迟 | 可第一时间使用 | Raw 占优 |
| 状态机/重连控制 | 中等 | 完全可控 | 产品后期 Raw 占优 |
| 跨语言迁移 | Python 绑定较强 | 任意 WS 客户端 | C++/Rust 时再切 |
| 调试协议 | 可监听原始 event | 完全透明 | 两者都能做 |
| 当前推荐度 | **9/10** | **7/10** | 先把产品跑起来 |

### 关键事实：DashScope Realtime 本身就是 WebSocket 包装

当前官方 `dashscope-sdk-python` 源码中，`OmniRealtimeConversation.connect()` 直接创建：

```python
self.ws = websocket.WebSocketApp(
    self.url,
    header=self._get_websocket_header(),
    on_message=self._on_message,
    on_error=self._on_error,
    on_close=self._on_close,
)
```

因此链路不是：

```text
Orin -> DashScope 中间服务器 -> Qwen Realtime
```

而是：

```text
Orin
  -> DashScope Python 包装代码（本机）
  -> 同一个 wss://.../api-ws/v1/realtime
  -> Qwen3.5 Omni Realtime
```

Raw WebSocket 的主要收益是**代码控制权**，不是绕掉一层云转发。

### DashScope Python 包的依赖情况

官方仓库当前 `requirements.txt` 主要包含：

```text
aiohttp
requests
websocket-client
cryptography
certifi
typer
rich
httpx
httpx-sse
```

没有阿里专用的 Linux ARM64 RTC 二进制运行库。需要注意，`cryptography`、PyAudio/PortAudio 等仍可能涉及平台 wheel 或系统原生库，但这与“必须等厂商给 Orin 编译 RTC SDK”是两类问题。

---

## 2. 推荐的软件分层

不要让你的机器人业务代码直接到处调用 `conv.xxx()`。

建议抽一层：

```text
┌──────────────────────────────────────┐
│ Robot / Voice Agent Business Logic   │
│                                      │
│ Tool Router / ROS2 / GPIO / CAN      │
│ Conversation State / Interrupt       │
└──────────────────┬───────────────────┘
                   │
          RealtimeTransport 接口
                   │
        ┌──────────┴──────────┐
        │                     │
 DashScopeTransport      RawWsTransport
      （现在）             （未来可换）
        │
        ▼
 Qwen3.5-Omni-Flash-Realtime
```

建议你的接口至少只有这些：

```python
class RealtimeTransport:
    def connect(self): ...
    def update_session(self, config): ...
    def send_audio(self, pcm_bytes): ...
    def send_tool_result(self, call_id, result): ...
    def create_response(self): ...
    def cancel_response(self): ...
    def close(self): ...
```

业务逻辑只订阅统一事件：

```text
speech_started
speech_stopped
user_transcript_delta
user_transcript_done
assistant_audio_delta
assistant_transcript_delta
function_call_done
response_done
error
```

这样未来从 DashScope 切 Raw WS，不会重写 ROS、Tool Router、麦克风、播放器和对话状态机。

---

## 3. 模型能力速查

模型：`qwen3.5-omni-flash-realtime`

### 支持

- 输入：Text / Image / Video / Audio
- 输出：Text / Audio
- Function Calling
- 原生 WebSearch
- `server_vad`
- `semantic_vad`
- 智能语义打断
- WebSocket
- WebRTC
- AOQ
- 自定义音色/声音复刻
- 输入/输出音频格式和采样率可配置

### 不支持或限制

- Realtime Function Calling 不支持 `tool_choice`
- 不支持 `parallel_tool_calls`
- **自定义 `tools` 与原生 `enable_search` 不能同时开启**
- 单次会话最长 120 分钟
- Flash Realtime 对话历史：音频最多 80 轮、480 秒；视频最多 50 轮、120 秒，超过后较早上下文会被丢弃
- 结构化输出、上下文缓存、批量推理、模型微调均不是该模型当前能力

### 上下文与限流

- 上下文：262,144 tokens
- 最大输入：196,608 tokens
- 最大输出：65,536 tokens
- 北京：60 RPM、100,000 TPM
- 新加坡：60 RPM、100,000 TPM

> 价格会变，正式预算前以模型页/控制台最新价格为准。调研日模型页北京原价：音频输入 ¥27/百万 tokens；文本+音频输出 ¥107/百万 tokens；文本/图片/视频输入 ¥3.3/百万 tokens；纯文本输出 ¥20/百万 tokens。

---

## 4. 连接方式与地域

北京：

```text
wss://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime
```

新加坡：

```text
wss://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com/api-ws/v1/realtime
```

Raw WebSocket 时模型通过 query 指定：

```text
?model=qwen3.5-omni-flash-realtime
```

鉴权：

```text
Authorization: Bearer DASHSCOPE_API_KEY
```

官方建议使用业务空间专属域名，而不是旧 `dashscope.aliyuncs.com` 域名，以获得更好的性能和稳定性。

### 你的 Orin 应该选什么协议

当前：**WebSocket**。

原因：

- 任意支持 WebSocket 的 ARM64 Linux 都能接；
- 已经实测在你的 Orin 上工作；
- Tool Calling 协议完整；
- 不依赖 RTC 平台适配；
- 比较容易抓包、记日志、重放事件。

WebSocket 的弱点是官方明确写着**没有内置 AEC/降噪**。机器人扬声器和麦克风同时工作时，这会成为后续产品化重点。

WebRTC/AOQ 的价值主要在弱网、AEC、降噪、媒体实时传输，但对你当前 Orin 环境会引入新的媒体栈和平台适配成本。AOQ 官方平台列表目前以 Android / iOS / HarmonyOS 为主，因此不把它放进 Orin 当前主线。

---

## 5. 音频配置

Qwen3.5 Omni Realtime 当前推荐使用新的 `audio.input.format` / `audio.output.format` 结构；DashScope SDK 对应 `AudioFormatConfig`。

### 可选格式

- `pcm`
- `wav`

### 可选采样率

- 8000 Hz
- 16000 Hz
- 24000 Hz
- 48000 Hz

默认：

- 输入：PCM、16kHz、mono、16bit
- 输出：PCM、24kHz、mono、16bit

### 对你的推荐

保持：

```text
Mic: 16000 Hz / mono / int16 PCM
Speaker: 24000 Hz / mono / int16 PCM
```

先别为了“规格更高”上 48kHz，网络和处理量会增大，实时语音助手收益通常不成比例。

### 音频 chunk

你当前：

```python
mic.read(3200)
```

16kHz 下：

```text
3200 samples / 16000 = 200ms
```

建议先试：

```python
AUDIO_CHUNK_FRAMES = 1600   # 100ms
```

100ms 会让发送节奏、VAD观察和打断粒度更细。

如果后续链路稳定，可以 A/B 50ms：

```python
AUDIO_CHUNK_FRAMES = 800
```

不要为了“更实时”无限减小，否则 WebSocket JSON/Base64 和线程调度开销会上升。

---

## 6. VAD：这是你当前最高优先级问题

### 先记住三个旋钮

| 参数 | 控制什么 | 官方范围/默认 | 你的起步值 |
|---|---|---:|---:|
| `turn_detection_type` | 如何判断一句话结束 | `server_vad` 默认 | **`semantic_vad`** |
| `turn_detection_threshold` | 声音被判为语音的敏感度 | -1~1，协议文档默认 0.5 | **0.5** |
| `turn_detection_silence_duration_ms` | 停多久才算说完 | 200~6000，默认 800ms | **1200ms** |
| `prefix_padding_ms` | 语音起点前保留多少音频 | SDK 当前默认 300ms | **300ms** |

### `server_vad` vs `semantic_vad`

`server_vad`：主要依据声学特征。

`semantic_vad`：结合语义有效性判断，可过滤回应语、背景音等无意义声音，仅 Qwen3.5 Omni Realtime 支持。官方在 Qwen3.5 Omni Realtime 的会话配置示例中推荐 `semantic_vad`。

你的问题是“自然停顿时模型把我截断”，所以先用：

```python
turn_detection_type="semantic_vad"
turn_detection_threshold=0.5
turn_detection_silence_duration_ms=1200
prefix_padding_ms=300
```

### 一个非常值得警惕的 SDK 默认值不一致

阿里官方 API / Python SDK 文档写：

```text
turn_detection_threshold 默认 0.5
```

但是当前 `dashscope-sdk-python` 主分支源码中，`OmniRealtimeConversation.update_session()` 的函数签名是：

```python
turn_detection_threshold: float = 0.2
```

并且 SDK 会把这个值显式写入：

```python
self.config["turn_detection"] = {
    "type": turn_detection_type,
    "threshold": turn_detection_threshold,
    "prefix_padding_ms": prefix_padding_ms,
    "silence_duration_ms": turn_detection_silence_duration_ms,
}
```

因此：**不要依赖默认值。全部显式传。**

这不一定单独解释“提前结束”，因为 silence duration 更直接控制句尾，但 0.2 会让弱声音/环境噪声更容易进入语音判定，对 Orin 风扇、扬声器漏音、麦克风底噪场景尤其不值得冒险。

### 推荐三套 Preset

#### A. 你现在的自然对话模式

```python
turn_detection_type="semantic_vad"
turn_detection_threshold=0.5
turn_detection_silence_duration_ms=1200
prefix_padding_ms=300
```

如果仍然容易截断：

```text
1200 -> 1400 -> 1600ms
```

#### B. 机器人短命令模式

适合“停”“左转”“过来”“看这里”：

```python
turn_detection_type="semantic_vad"
turn_detection_threshold=0.5
turn_detection_silence_duration_ms=500  # 或 600~800
```

#### C. 嘈杂设备环境

```python
turn_detection_type="semantic_vad"
turn_detection_threshold=0.6
turn_detection_silence_duration_ms=1200
```

如果人声比较小、远场麦克风容易漏：不要盲目继续升 threshold，否则会漏掉真实语音。

### VAD 日志必须保留

监听：

```text
input_audio_buffer.speech_started
input_audio_buffer.speech_stopped
```

建议打：

```python
print(time.monotonic(), "VAD START")
print(time.monotonic(), "VAD STOP")
```

#### 判断规则

如果你故意说：

```text
“我现在想让你……嗯……帮我看一下”
```

在自然停顿处收到 `speech_stopped`，优先调大 `silence_duration_ms`。

如果你完全连续讲话，中间没有真实静音，却收到 `speech_stopped`，先查：

- 麦克风/USB 丢帧
- ALSA/PyAudio buffer
- 输入线程阻塞
- 网络发送 cadence
- AEC/降噪误杀
- 扬声器回授

不要靠把 silence 直接拉到 3000ms 给音频链路故障“擦屁股”。

### Manual 模式是很好的诊断工具

可以临时：

```python
enable_turn_detection=False
```

此时客户端自己判断一句话结束，然后：

```text
commit -> create_response
```

如果 Manual 模式完全没有乱切，说明主要问题集中在服务端 VAD / 声学输入，而不是模型生成。

Manual 不一定适合最终全双工体验，但非常适合排错。

---

## 7. AEC、回声与“模型自己打断自己”

官方 DashScope Realtime SDK 文档专门提醒：建议耳机播放，避免回声触发语音打断。

机器人不可能长期戴耳机，因此量产阶段需要认真做：

```text
Speaker PCM
    │
    ├──────────► Speaker
    │
    ▼
AEC Reference
    │
Mic ──► AEC/NS ──► Realtime
```

### 最简单的排查实验

1. 戴耳机/关闭扬声器，连续测试 20 轮；
2. 再打开扬声器，保持其他条件相同；
3. 比较 `speech_started` 误触发和提前 `speech_stopped` 次数。

如果问题只在扬声器打开后显著上升，先做 AEC，不要继续玄学调 VAD。

---

## 8. Realtime Function Calling：正确融合方式

不要再用普通：

```python
MultiModalConversation.call(...)
```

Realtime 的工具调用就在当前 WebSocket session 内完成。

### 完整时序

```text
Mic PCM
  │
  ▼
append_audio
  │
  ▼
VAD 检测用户结束
  │
  ▼
Qwen 判断要调用工具
  │
  ▼
response.function_call_arguments.done
  │
  ├─ name
  ├─ arguments
  └─ call_id
  │
  ▼
Orin 本地执行 Tool
  │
  ▼
conversation.item.create(function_call_output)
  │
  ▼
response.create
  │
  ▼
response.audio.delta
  │
  ▼
Speaker
```

**工具函数可以直接在 Orin 本地执行，不要求公网 URL。**

### Tools 定义

```python
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "move_robot",
            "description": "控制机器人移动",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "enum": ["left", "right", "forward", "backward", "stop"]
                    }
                },
                "required": ["direction"]
            }
        }
    }
]
```

### DashScope Session 配置

```python
conv.update_session(
    output_modalities=[MultiModality.AUDIO, MultiModality.TEXT],
    voice="Ethan",
    instructions=instructions,

    enable_input_audio_transcription=True,

    enable_turn_detection=True,
    turn_detection_type="semantic_vad",
    turn_detection_threshold=0.5,
    turn_detection_silence_duration_ms=1200,
    prefix_padding_ms=300,

    tools=TOOLS,
    enable_search=False,
)
```

### 收 Tool Call

监听：

```python
elif event_type == "response.function_call_arguments.done":
    name = response["name"]
    args = json.loads(response["arguments"])
    call_id = response["call_id"]
```

官方建议以 `done` 中的完整 `arguments` 为准，不必依赖前面的 delta 自己拼最终参数。

### Orin 本地执行

例如：

```python
result = TOOL_FUNCTIONS[name](**args)
```

这里可以接：

- ROS2 service/action
- GPIO
- CAN
- Serial
- Camera
- 本地 SQLite/Redis
- 设备状态
- 本地视觉模型
- 本地导航模块

### 回传结果

```python
conv.create_item({
    "type": "function_call_output",
    "call_id": call_id,
    "output": str(result),
})

conv.create_response(
    output_modalities=[MultiModality.AUDIO, MultiModality.TEXT]
)
```

### 一个重要行为

官方 Python SDK 文档说明：命中工具调用时，模型不生成音频，只返回工具参数。工具结果回传并 `create_response()` 后才生成最终回复。

这对机器人很好，因为不会出现：

```text
“好的我现在帮你打开灯……”
（嘴已经说了，但 Tool 其实还没成功）
```

你可以让执行结果成为真正的事实来源。

### Function Calling 限制

Realtime 当前：

```text
tool_choice: 不支持
parallel_tool_calls: 不支持
```

所以复杂 Agent 不要假设它能一次并行扔出 5 个工具。

如果需要并发读取多个本地状态，一个实用做法是自己定义聚合工具：

```text
get_robot_snapshot()
```

由 Orin 本地并发获取：

```text
battery + pose + camera_state + temperature
```

再一次性返回模型。

---

## 9. Tools 与 WebSearch 的冲突

当前官方限制：

```text
tools + enable_search 不能同时开启
```

你的硬件 Agent 主线应该：

```python
tools=TOOLS
enable_search=False
```

如果既要本地工具，又要联网搜索，建议把搜索也包装成你自己的工具：

```text
search_web(query)
```

由你的后端/服务执行搜索，再把结果作为 `function_call_output` 回给 Realtime。

这样模型眼中所有外部动作都是统一 Tool Router，架构更干净。

---

## 10. 客户端事件速查

你最可能用到的事件：

| 客户端 -> 服务端 | 用途 |
|---|---|
| `session.update` | 配 voice、VAD、tools、instructions、音频格式 |
| `input_audio_buffer.append` | 发送 Base64 音频 |
| `input_audio_buffer.commit` | Manual 模式提交输入 |
| `input_audio_buffer.clear` | 清空未提交输入 |
| `conversation.item.create` | 回传 `function_call_output` |
| `response.create` | Tool 结果后触发最终回复，或 Manual 模式触发回复 |
| `response.cancel` | 取消正在生成的模型响应 |
| `session.finish` | 正常结束会话（SDK/服务版本行为需要在你的环境回归测试） |

> VAD 模式下，普通用户语音结束后服务端会自动提交并触发响应，不需要每轮手动 `response.create`。Tool Result 回传后需要 `response.create`。

---

## 11. 服务端事件速查

### 会话

```text
session.created
session.updated
```

每次配置后，建议把 `session.updated.turn_detection` 打出来，确认服务端真正收到的是你想要的参数。

### VAD

```text
input_audio_buffer.speech_started
input_audio_buffer.speech_stopped
input_audio_buffer.committed
```

### 用户 ASR

```text
conversation.item.input_audio_transcription.delta
conversation.item.input_audio_transcription.completed
conversation.item.input_audio_transcription.failed
```

输入转录模型当前固定为：

```text
qwen3-asr-flash-realtime
```

### 模型音频

```text
response.audio.delta
response.audio.done
```

### 模型语音对应文本

```text
response.audio_transcript.delta
response.audio_transcript.done
```

### Tool

```text
response.function_call_arguments.delta
response.function_call_arguments.done
```

执行工具时优先使用 `done.arguments`。

### 完整响应

```text
response.created
response.done
```

`response.done` 里还能看到 usage，可以做成本和性能统计。

### 错误

```text
error
```

务必完整记录 `event_id`、error code、message、request/session identifiers，方便对照官方错误码或提交工单。

---

## 12. 打断与播放器

你当前 Demo 如果在 `on_event()` 里直接：

```python
self.out.write(audio)
```

能跑，但不是最终形态。

推荐：

```text
WebSocket callback
      │
      ▼
Base64 decode queue
      │
      ▼
PCM playback queue
      │
      ▼
独立播放线程
```

当收到：

```text
input_audio_buffer.speech_started
```

如果判定是真正用户打断：

1. `response.cancel()`；
2. 清本地尚未播放的 PCM queue；
3. 继续采集用户语音。

阿里官方 Function Calling Demo 本身也采用了解码/播放双线程的 PCMPlayer，默认按 100ms chunk 播放，以降低取消播放的粒度。

---

## 13. 建议记录的延迟指标

不要只说“感觉快”，把它变成可以 A/B 的数字。

每轮记录：

```text
t0 = speech_stopped

t1 = input transcription completed

t2 = response.created

t3 = function_call_arguments.done        # 如果有 Tool

t4 = local tool done                     # 如果有 Tool

t5 = response.create sent after tool     # 如果有 Tool

t6 = first response.audio.delta

t7 = speaker actually starts playback

t8 = response.audio.done
```

核心指标：

### 无工具

```text
EoU -> First Audio = t6 - t0
EoU -> Audible       = t7 - t0
```

### 有工具

```text
EoU -> Tool Call     = t3 - t0
Tool Execution       = t4 - t3
Tool Result -> Audio = t6 - t5
Total Audible        = t7 - t0
```

至少统计：

```text
P50 / P90 / P95
```

不要只看平均值。

DashScope SDK 还提供首文本/首音频延迟相关 getter，可作为辅助指标，但建议仍保留你自己的 monotonic timestamp，这样能覆盖本地 Tool 和扬声器缓冲。

---

## 14. 你当前代码的推荐配置片段

建议先把模型切到 Flash：

```python
model = "qwen3.5-omni-flash-realtime"
```

Session：

```python
conv.update_session(
    output_modalities=[MultiModality.AUDIO, MultiModality.TEXT],
    voice="Ethan",
    instructions=instructions,

    enable_input_audio_transcription=True,

    enable_turn_detection=True,
    turn_detection_type="semantic_vad",
    turn_detection_threshold=0.5,
    turn_detection_silence_duration_ms=1200,
    prefix_padding_ms=300,

    tools=TOOLS,
    enable_search=False,
)
```

麦克风：

```python
AUDIO_CHUNK_FRAMES = 1600  # 16kHz 下约 100ms

mic = pya.open(
    format=pyaudio.paInt16,
    channels=1,
    rate=16000,
    input=True,
    input_device_index=mic_index,
    frames_per_buffer=AUDIO_CHUNK_FRAMES,
)
```

主循环中不需要额外：

```python
time.sleep(0.01)
```

因为阻塞式 `mic.read()` 已经按实时音频节奏等待。

---

## 15. 推荐测试矩阵

不要一次改十个参数。按以下顺序：

### Test 1：先验证 Flash

```text
Plus -> Flash
其余完全不变
```

比较 P50/P95 EoU -> Audible。

### Test 2：VAD 类型

```text
server_vad / 1200ms / threshold 0.5
vs
semantic_vad / 1200ms / threshold 0.5
```

用同一组自然停顿句子测试。

### Test 3：Silence

```text
800
1000
1200
1400
1600ms
```

记录：

- 抢答次数
- 用户主观等待感
- EoU -> First Audio

### Test 4：Threshold

保持 1200ms，只测：

```text
0.4 / 0.5 / 0.6
```

分别在：

- 安静房间
- Orin 风扇开启
- 扬声器播放中

测试误触发。

### Test 5：Chunk Size

```text
200ms（3200 frames）
100ms（1600 frames）
50ms（800 frames）
```

不要预设越小越好，以 P95 和 CPU 占用为准。

### Test 6：AEC 隔离

```text
耳机
vs
外放无 AEC
vs
外放 + AEC
```

这是判断“到底是 VAD 还是回声”的关键实验。

### Test 7：Tool Loop

先做一个完全本地、几乎零耗时的工具：

```text
get_battery_mock()
```

验证协议延迟后，再接 ROS2 / GPIO / CAN。

---

## 16. 故障诊断树

### 症状：我没说完，它就回答

```text
有真实停顿 > 800ms？
  ├─ 是 -> silence_duration_ms 调 1000/1200/1400
  └─ 否 -> 查音频流/丢帧/AEC/噪声处理
```

优先：

```text
semantic_vad + threshold=0.5 + silence=1200
```

### 症状：风扇/背景声总触发 speech_started

```text
threshold 0.5 -> 0.6
```

同时检查麦克风增益和 NS。

### 症状：模型自己说话时突然认为用户插话

第一嫌疑：扬声器回声。

先用耳机验证，再做 AEC。

### 症状：Function Call 不触发

检查：

1. `session.updated` 是否真的包含 `tools`；
2. `enable_search` 是否误设为 True；
3. tool `description` 是否清晰；
4. system instructions 是否告诉模型该工具什么时候使用；
5. 是否在监听 `response.function_call_arguments.done`。

### 症状：收到 Tool Call，但模型不继续说

检查流程是否缺了：

```text
conversation.item.create(function_call_output)
然后
response.create
```

### 症状：Raw WS 看起来比 DashScope “理论上更快”

先别重写。两者连的是同一 Realtime WSS。先用 timestamp 证明本机 SDK 层真的贡献了可感知的延迟，再决定是否值得维护一套协议状态机。

---

## 17. DashScope -> Raw WebSocket 的未来迁移映射

| DashScope 方法 | Raw WS 事件 |
|---|---|
| `connect()` | 建立 WSS |
| `update_session(...)` | `session.update` |
| `append_audio(base64)` | `input_audio_buffer.append` |
| `commit()` | `input_audio_buffer.commit` |
| `clear_audio()`/等价方法 | `input_audio_buffer.clear` |
| `create_item(...)` | `conversation.item.create` |
| `create_response(...)` | `response.create` |
| `cancel_response()` | `response.cancel` |
| callback `on_event()` | 直接解析服务端 JSON event |

所以现在用 DashScope 并不会把你锁死。只要业务代码不直接散落调用 SDK，未来换 Raw 很机械。

---

## 18. 官方文档索引：建议收藏

以下均为阿里云/官方源码，按重要性排序。

### P0：开发时常驻打开

1. **Qwen-Omni-Realtime 总文档**
   https://help.aliyun.com/zh/model-studio/realtime
   用途：连接、Session、VAD、音频输入输出、模型选型、限制、快速开始。

2. **DashScope Python SDK Realtime API**
   https://help.aliyun.com/zh/model-studio/omni-realtime-python-sdk
   用途：`OmniRealtimeConversation`、`update_session`、VAD、tools、音频配置、SDK 最低版本。

3. **客户端事件**
   https://help.aliyun.com/zh/model-studio/client-events
   用途：你往服务器发什么 JSON，Raw WS 的“处方表”。

4. **服务端事件**
   https://help.aliyun.com/zh/model-studio/server-events
   用途：VAD、ASR、音频、Tool Call、response.done、error 的完整事件定义。

5. **Function Calling**
   https://help.aliyun.com/zh/model-studio/qwen-function-calling
   用途：Realtime 专属 Tool Loop，含 DashScope 和 Raw WebSocket 完整示例。

### P1：设计/选型

6. **qwen3.5-omni-flash-realtime 模型页**
   https://help.aliyun.com/zh/model-studio/qwen3-5-omni-flash-realtime
   用途：能力、上下文、价格、限流、快照版本。

7. **Realtime API 概述**
   https://help.aliyun.com/zh/model-studio/realtime-api-overview
   用途：AOQ / WebRTC / WebSocket 的官方比较。

8. **实时多模态交互流程**
   https://help.aliyun.com/zh/model-studio/omni-realtime-interaction-process
   用途：VAD/Manual 生命周期和整体交互流程。

9. **音色列表**
   https://help.aliyun.com/zh/model-studio/omni-voice-list
   用途：Qwen Omni Realtime 可用 voice。

10. **声音复刻**
    https://help.aliyun.com/zh/model-studio/qwen-omni-voice-cloning
    用途：给 Realtime 使用自定义音色。

### P2：上线/运维

11. **限流**
    https://help.aliyun.com/zh/model-studio/rate-limit

12. **错误码**
    https://help.aliyun.com/zh/model-studio/error-code

13. **联网搜索**
    https://help.aliyun.com/zh/model-studio/web-search/
    注意：Realtime 原生 WebSearch 与 custom tools 当前不能同时开启。

### 官方代码

14. **阿里云百炼语音 Demo：Omni Python**
    https://github.com/aliyun/alibabacloud-bailian-speech-demo/tree/master/samples/conversation/omni/python
    用途：VAD、播放器、Function Calling 的可运行工程示例。

15. **DashScope Python SDK 源码**
    https://github.com/dashscope/dashscope-sdk-python
    Realtime 实现：
    https://github.com/dashscope/dashscope-sdk-python/blob/main/dashscope/audio/qwen_omni/omni_realtime.py
    用途：当文档与实际行为不一致时，以你安装版本的源码和服务端 `session.updated` 回显一起核对。

---

## 19. 最终建议

### 现在的技术路线

```text
Jetson Orin ARM64
        │
        ├─ ALSA/PyAudio Mic
        │
        ├─ Local AEC/NS（逐步加入）
        │
        ▼
DashScope OmniRealtimeConversation
        │
        │ 同一条 WebSocket
        ▼
qwen3.5-omni-flash-realtime
        │
        ├─ Audio response
        │
        └─ Function Call
              │
              ▼
          Orin Local Tool Router
              │
              ├─ ROS2
              ├─ GPIO
              ├─ CAN
              ├─ Serial
              ├─ Camera
              └─ Local models/state
```

### 决策一句话

**DashScope 现在是“省代码的 Raw WebSocket 包装”，不是需要逃离的 ARM64 黑盒 SDK。先用它把 VAD、AEC、打断和 Tool Loop 做稳；Transport 层留接口，未来需要 C++/Rust 或协议级控制时再切 Raw WebSocket。**

### 你下一次提交代码前，至少显式写死这四个值

```python
turn_detection_type="semantic_vad"
turn_detection_threshold=0.5
turn_detection_silence_duration_ms=1200
prefix_padding_ms=300
```

然后检查 `session.updated` 的回显，不再相信默认值。

---

## 20. 调研备注

- 文档与 SDK 可能独立更新。本手册中最值得持续关注的已知差异是：协议/官方参数文档写 VAD threshold 默认 0.5，而调研日 DashScope Python SDK `main` 源码的 `update_session()` 默认值为 0.2。
- 上线时应固定 DashScope 版本并将版本号写入日志，例如：`dashscope.__version__` 或 `importlib.metadata.version("dashscope")`。
- Dedicated Python SDK 文档当前要求版本不低于 1.26.5。不要只参考旧 Quick Start 中较低的版本要求。
- 生产环境应把所有关键 Realtime event 和 monotonic latency 指标结构化记录，模型/SDK 升级后做固定语料回归。
