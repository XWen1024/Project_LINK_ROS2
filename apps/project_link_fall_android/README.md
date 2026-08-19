# LINK 跌倒守护 Android MVP

当前版本：`0.2.1 (3)`。

单手机、单页面的现场演示 App。支持手机 IMU 疑似跌倒检测、安全演示触发、局域网通知 Orin、15 秒取消窗口和本地模拟后端。

## 构建

使用 Android Studio 打开本目录，或在 Windows PowerShell 中执行：

```powershell
$env:JAVA_HOME='C:\Program Files\Android\Android Studio\jbr'
$env:ANDROID_HOME="$env:LOCALAPPDATA\Android\Sdk"
.\gradlew.bat :app:assembleDebug
```

APK 输出：`app/build/outputs/apk/debug/app-debug.apk`。

## 首次演示

1. 安装并打开 App。
2. 点击“设置”，保持“本地模拟后端”开启并保存。
3. 切换至“演示跌倒”。
4. 点击“模拟跌倒”，确认通知影响后等待 5 秒。
5. App 将模拟 Orin 接收、扫描、视觉研判和联系人通知。

连接真实 Orin 时关闭本地模拟，填写 `http://<orin-ip>:<port>` 和共享 Token 后保存。App 回到首页后会立即检查连接并弹出结果；App 位于前台时还会每 15 秒执行一次静默健康检查，自动更新首页的 Orin 状态。

“测试连接”只用来诊断当前设置窗口里尚未保存的草稿，不会修改首页的已保存连接状态。测试和保存后的检查都会弹出完整诊断信息：实际目标 URL、手机当前网络类型、Token 是否已填写、HTTP 状态码、后端 readiness 和 JSON 响应。手机端禁止填写 `127.0.0.1`、`localhost` 或 `0.0.0.0`；这些地址不会指向 Orin。

## Orin HTTP 约定

- `GET /health`
- `POST /api/fall`
- `GET /api/fall/{event_id}`
- `POST /api/fall/{event_id}/cancel`
- 请求头：`X-Fall-Guard-Token: <token>`
- 状态：`accepted`、`scanning`、`verifying`、`notified`、`not_fall`、`cancelled`、`failed`

Orin 必须把 `event_id` 作为幂等键，并在请求中的 `cancel_window_ms` 结束前禁止向紧急联系人发送通知。

完整 Orin 后端实现契约见：`docs/modules/sensors/fall-response/ANDROID_ORIN_HANDOFF.md`。

## 当前验证边界

- Windows 已验证编译、单元测试和 Lint。
- 小米 2304FPN6DC（Android 16 / API 36）已完成安装、冷启动和主要按钮手工验收。
- 2026-08-19 已通过手机到 `http://10.255.176.119:8765/health` 的真实 Token 健康检查，返回 HTTP 200，所有后端 readiness 为 `true`。
- `0.2.1 (3)` 的保存后自动检查、前台 15 秒心跳和设置草稿隔离已通过 Windows 编译、单元测试和 Lint；尚需在真机重新连接 ADB 后完成交互验收。
- 仍需验证通知权限后的长时间锁屏后台采样和 MIUI 电池优化策略。
- IMU 阈值是原型初值，不能作为医疗级跌倒检测依据。
- 人员不要通过真实摔倒测试；使用软垫、假人或受控放置手机。
