# Volc WebSocket S2S 延迟优化审查

日期：2026-08-12

范围：只审查独立 Spike 和官方 Embedded Kit low-load WebSocket SDK，不修改
Legacy 语音链路。

SDK commit：`2c94f96f3aad4094e0e818cbb031149fd4384ead`

## 结论

当前首反馈约 2.8 秒中，最大的块不是 Orin 本地执行，也不是 JSON/Base64，
而是云端完成 Function Calling 决策的时间。SDK 内部的常规性能优化无法消除
这约 1.9 秒。

本地最值得优先优化的三件事是：

1. 更早、更可控地确定用户说完，减少结束点/VAD 尾巴。
2. 保留长连接并缓存设备注册结果，避免每次启动的 HTTP/TLS 注册和偶发
   60 秒失败。
3. 将 SDK 的网络接收线程与业务回调、磁盘写入、Tool 执行彻底解耦，降低
   ROS 高负载下的尾延迟和死锁风险。

Function 成功后的反馈路径已经有明确结论：动态话术优先使用 `input_tts`，
固定短话术追求极限延迟时使用本地预生成 PCM。

## 当前延迟预算

Seed 2.1 Turbo、B1 Prompt/Tool、固定 PCM、`input_tts` 的 10 次成功样本：

| 阶段 | 平均值 | 占最后输入到首反馈的比例 | 可控性 |
|---|---:|---:|---|
| 最后输入 -> 服务端 VAD stop | 360.6 ms | 13.0% | 部分可控 |
| VAD stop -> Function Call | 1932.1 ms | 69.6% | 基本是云端黑盒 |
| Function Call -> 参数完成 | 60.1 ms | 2.2% | 很小 |
| 本地 Tool + 结果发送 | 0.5 ms | <0.1% | 已经足够快 |
| `input_tts` -> 首音频 | 423.3 ms | 15.2% | 可选择本地 PCM |
| 最后输入 -> 首反馈音频 | 2776.6 ms | 100% | — |

因此，单纯把 C 代码里的 `malloc` 优化掉，不会带来秒级改善。即使把 SDK
本地处理全部压到零，仍然存在大约 2.3 秒的 VAD、云端 FC 和云端首音频时间。

## P0：结束点和 VAD 控制

这是每轮对话中最大的本地可尝试优化点。

当前证据：

- Turbo 最后输入到 VAD stop 平均 `360.6 ms`，P90 `471 ms`。
- Pure S2S 组平均 `693.2 ms`，个别样本超过 1 秒。
- SDK 的 `__ws_parse_params()` 只读取 `audio.codec`。
- PCM 模式下 `__ws_wait_for_session_update()` 固定返回 false。
- SDK 生成的 `session.update` 只包含 model 和 input audio format，没有暴露
  server VAD 阈值、静音持续时间或 endpointing 参数。

建议改造方向：

1. 给 WS params 增加一个原样透传的 session 配置，而不是 SDK 自己只挑
   `audio.codec`。
2. 先通过协议探针确认服务端是否接受 VAD threshold、silence duration 或
   turn detection 配置；没有服务端确认前不猜字段。
3. 如果协议允许关闭 server VAD，则由上层在确定说话结束时立即发送
   `input_audio_buffer.commit` 和 `response.create`。
4. A/B 测试 20/40/100 ms 音频帧。官方 sample 使用 100 ms；更小帧可能使
   增量 ASR 和结束边界更细，但会增加 Base64/JSON/WS 帧开销。

现实收益预估：平均约 100–300 ms，尾部样本可能更多。它比改 Base64 更有
价值，但无法消除约 1.9 秒的云端 FC 决策。

## P0：持久化设备身份和长连接

这项主要优化首次交互时间和可靠性，不改变同一连接内的单轮 FC 延迟。

源码现状：

- `volc_create()` 每次都同步调用 `volc_device_register()`。
- 注册每次创建新的 HTTP client、DNS/TCP/TLS 连接，完成后立即关闭。
- 当前 Turbo 成功样本注册平均 `687.5 ms`，连接平均 `393.9 ms`。
- B1 和 Turbo 各出现一次 mbedTLS `-0x7280`，失败尝试约 `60.5 s`。

建议：

1. 把 engine/WS 设计为进程级长生命周期对象，不要每轮对话 create/destroy。
2. 明确 device secret 的有效期和轮换规则；若官方允许持久化，使用权限
   `0600` 的本地缓存，并在认证失败时重新注册。
3. 将注册、WSS 连接和会话 readiness 拆成明确状态，后台重连，业务层只在
   ready 后接受语音。
4. TLS 可进一步研究 session resumption，但优先级低于直接保持 WSS 长连接。

预期：冷启动通常节省约 1.0 秒，并显著降低注册 TLS 偶发失败对用户的影响。

## P1：网络线程与回调解耦

这是 SDK 中最值得修的结构性问题。

源码现状：

- `websocket.c` 在持有 client mutex 时执行 `ws_client_recv()`。
- 接收函数同步 dispatch event，随后同步进入用户 message/audio callback。
- 同一个 mutex 也保护所有 WS send。
- 如果用户 callback 内做磁盘 I/O、复杂 JSON、Tool 执行，网络接收被阻塞；
  如果 callback 直接同步调用 send，还存在重新获取 normal mutex 的死锁风险。
- Smoke Test 的 audio callback 同步写 PCM、写 WAV 并 `fflush()`；这适合取证，
  不适合作为生产播放路径。

建议：

1. 网络线程只负责读帧、组帧和投递轻量事件。
2. 在释放 socket mutex 后再调用上层 callback。
3. audio、message、Tool 分别进入有界队列；播放线程、Tool worker 和日志线程
   独立消费。
4. 音频队列满时采用明确策略并计数，不能无限积压。
5. 生产代码禁止在音频 callback 中逐包 `fflush()`。

这项在空闲 Orin 上可能只省几毫秒，但在 ROS、SLAM、Nav2 同时运行时，主要
价值是降低 P90/P99 抖动、音频卡顿和偶发死锁。

## P1：减少音频热路径的堆分配和复制

源码现状：

- 每个输入帧都 Base64 编码，再创建 cJSON tree，再序列化成新 JSON buffer。
- `volc_json_read_string()` 每读取一个字段都会分配路径 buffer 和字符串副本。
- 收到 `response.audio.delta` 时，大块 Base64 delta 被 cJSON 保存一次，又被
  `volc_json_read_string()` 完整复制一次，然后再次分配 PCM decode buffer。
- assembler 扩容时 `realloc`，每个完整消息后对整个 capacity 做 `memset`。
- WS send 还会复制到固定 tx buffer 后再写 socket。

建议：

1. 对已解析的 cJSON 直接使用借用的 `valuestring`，不要复制大块 delta。
2. 为 Base64 输入和 PCM 输出维护可增长复用 buffer。
3. 输入音频 JSON 用预构造 prefix/suffix 或专用 serializer，避免每帧创建
   cJSON tree。
4. assembler 只重置 size，不必清零整个 capacity；仅保证末尾 NUL。
5. 为热路径增加 CPU 时间、分配次数和最大队列深度指标。

预期收益主要是降低 CPU、内存抖动和高负载尾延迟。根据当前
`response.created -> first audio` 只有 0–7 ms，这不是当前秒级延迟来源。

## P2：Socket 和小消息发送

可做的实验：

- 在 TCP socket 上设置 `TCP_NODELAY`，观察 commit、Function Output 和
  `input_tts` 小消息的 P90 是否下降。
- 将 final audio append、commit、response.create 的连续写入做批量化，减少
  TLS record 和 syscall 数量，但必须保持协议事件边界。
- 当前 WebSocket task 每次 `select()` 最长 1000 ms，但数据到达会立即唤醒，
  这不是固定 1 秒延迟；不要因为看到 1000 ms 就直接改成忙轮询。

预期通常是 0–30 ms，必须用 A/B 数据证明，不能作为主优化方向。

## P2：Linux OSAL 调度语义

SDK 设置了 WebSocket task priority 和 stack size，但 Linux
`volc_osal_thread_create()` 直接调用 `pthread_create()`，完全忽略 name、priority
和 stack size。

建议：

- 设置 pthread name，便于 perf/ftrace 定位。
- 允许配置 stack size。
- 在无特权时保持普通调度；只有经过整机安全验证后才考虑实时优先级。
- 做一组 ROS 全栈高负载下的 P50/P90/P99 对照，而不是只看空闲机器。

## 已经证明有效的优化

| 策略 | Tool 成功 -> 首反馈平均 | 相对 D0 |
|---|---:|---:|
| D0 二次 LLM | 2138.2 ms | 基线 |
| D2 `input_tts` | 451.5 ms | 快 1686.7 ms，减少 78.9% |
| D3 本地 PCM | 0.4 ms | 接近立即反馈 |

这是目前收益最大、证据最强的本地改造。建议生产路径默认 D2，只有固定且
经过审核的短反馈使用 D3。

## 不建议优先投入的方向

- 为了几微秒重写普通 mutex。
- 把 1000 ms `select()` 改成高频 sleep/busy loop。
- 在没有协议证据时伪造 ASR/LLM/TTS 分段时间。
- 为追求“零复制”直接把网络 buffer 生命周期暴露给业务线程。
- 在完成长连接、endpointing、callback 解耦前大规模替换 mbedTLS。

## 推荐实施顺序

1. 增加 session/VAD 参数探针，做 server VAD 默认值与更短 endpoint 的 10 次
   A/B。
2. 做 20/40/100 ms frame cadence A/B。
3. 做长生命周期 engine 和设备注册缓存 Spike，单独测 cold/warm startup 和
   TLS 失败恢复。
4. 将 callback 改为有界队列异步派发，在 ROS 全栈负载下测 P90/P99。
5. 再做 borrowed JSON、复用 buffer、`TCP_NODELAY` 等微优化。

所有官方源码改动都应以独立 patch 放在
`experiments/volc_s2s_smoke/patches/`，逐项 A/B，不直接静默修改 third_party。

## 源码证据索引

- 每次 create 同步设备注册：
  `volc_conv_ai/src/volc_conv_ai.c:219-280`
- 动态注册 HTTP/TLS 请求：
  `volc_conv_ai/src/base/volc_device_manager.c:81-147`
- PCM session update 限制：
  `volc_conv_ai/src/transports/low_load/src/volc_ws.c:53-67,241-260`
- 输入音频 Base64、逐帧 cJSON 和 commit/response.create：
  `volc_conv_ai/src/transports/low_load/src/volc_ws.c:421-527`
- 音频 delta 解析、复制、decode 和同步 callback：
  `volc_conv_ai/src/transports/low_load/src/volc_ws.c:111-207`
- assembler realloc/copy/full memset：
  `volc_conv_ai/src/transports/low_load/src/volc_ws.c:263-303`
- 持有 client mutex 接收并同步 dispatch callback：
  `volc_conv_ai/src/transports/low_load/third_party/websocket/websocket.c:928-967,1389-1396`
- send 与 receive 共用 client mutex：
  `volc_conv_ai/src/transports/low_load/third_party/websocket/websocket.c:995-1041`
- 1000 ms select 是可唤醒等待，不是固定延迟：
  `volc_conv_ai/src/transports/low_load/third_party/websocket/websocket.c:1419-1424`
- Linux OSAL 忽略 thread name/priority/stack 参数：
  `volc_conv_ai/osal/src/linux/volc_osal.c:117-141`
- JSON helper 的逐字段分配和字符串复制：
  `volc_conv_ai/src/util/volc_json.c:33-75,112-132`
- Smoke Test callback 中的同步文件写入仅用于取证：
  `experiments/volc_s2s_smoke/src/main.c:712-784`
