# LINK 跌倒守护 Android MVP

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

连接真实 Orin 时关闭本地模拟，填写 `http://<orin-ip>:<port>` 和共享 Token，再点击“测试连接”。

## Orin HTTP 约定

- `GET /health`
- `POST /api/fall`
- `GET /api/fall/{event_id}`
- `POST /api/fall/{event_id}/cancel`
- 请求头：`X-Fall-Guard-Token: <token>`
- 状态：`accepted`、`scanning`、`verifying`、`notified`、`not_fall`、`cancelled`、`failed`

Orin 必须把 `event_id` 作为幂等键，并在请求中的 `cancel_window_ms` 结束前禁止向紧急联系人发送通知。

## 当前验证边界

- Windows 已验证编译、单元测试和 Lint。
- 真机必须验证通知权限、锁屏后台采样和厂商电池优化策略。
- IMU 阈值是原型初值，不能作为医疗级跌倒检测依据。
- 人员不要通过真实摔倒测试；使用软垫、假人或受控放置手机。
