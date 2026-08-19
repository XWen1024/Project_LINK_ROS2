package com.projectlink.fallguard

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBars
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.projectlink.fallguard.ui.theme.FallGuardTheme

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MainScreen(
    state: MainUiState,
    onModeChanged: (AppMode) -> Unit,
    onGuardianToggle: () -> Unit,
    onDemoRequested: () -> Unit,
    onDemoConfirmed: () -> Unit,
    onDemoDismissed: () -> Unit,
    onDemoCountdownCancelled: () -> Unit,
    onIncidentCancelled: () -> Unit,
    onSettingsRequested: () -> Unit,
    onSettingsDismissed: () -> Unit,
    onSettingsSaved: (AppSettings) -> Unit,
    onConnectionTested: (AppSettings) -> Unit,
    modifier: Modifier = Modifier,
) {
    Scaffold(
        modifier = modifier.fillMaxSize(),
        contentWindowInsets = WindowInsets(0, 0, 0, 0),
        topBar = {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .windowInsetsPadding(WindowInsets.statusBars)
                    .padding(horizontal = 20.dp, vertical = 12.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column {
                    Text(
                        text = "LINK 跌倒守护",
                        style = MaterialTheme.typography.titleLarge,
                        fontWeight = FontWeight.Bold,
                    )
                    Text(
                        text = "手机端 MVP",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                TextButton(onClick = onSettingsRequested) {
                    Text("设置")
                }
            }
        },
    ) { innerPadding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding)
                .windowInsetsPadding(WindowInsets.navigationBars)
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 20.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            StatusHero(state = state)

            SingleChoiceSegmentedButtonRow(modifier = Modifier.fillMaxWidth()) {
                AppMode.entries.forEachIndexed { index, mode ->
                    SegmentedButton(
                        selected = state.mode == mode,
                        onClick = { onModeChanged(mode) },
                        shape = SegmentedButtonDefaults.itemShape(index, AppMode.entries.size),
                        enabled = state.incident == null && state.demoCountdown == null,
                        label = { Text(if (mode == AppMode.REAL) "真实跌倒" else "演示跌倒") },
                    )
                }
            }

            ConnectionCard(state = state)

            if (state.mode == AppMode.REAL) {
                RealModeCard(state = state, onGuardianToggle = onGuardianToggle)
            } else {
                DemoModeCard(state = state, onDemoRequested = onDemoRequested)
            }

            state.incident?.let { incident ->
                IncidentCard(incident = incident, onCancel = onIncidentCancelled)
            }

            Text(
                text = "该功能用于现场演示与安全辅助，不属于医疗设备。",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(bottom = 12.dp),
            )
        }
    }

    if (state.demoConfirmationVisible) {
        DemoConfirmationDialog(
            onConfirm = onDemoConfirmed,
            onDismiss = onDemoDismissed,
        )
    }

    state.demoCountdown?.let { seconds ->
        CountdownDialog(seconds = seconds, onCancel = onDemoCountdownCancelled)
    }

    if (state.settingsVisible) {
        SettingsDialog(
            initialSettings = state.settings,
            testingConnection = state.testingConnection,
            onDismiss = onSettingsDismissed,
            onSave = onSettingsSaved,
            onTestConnection = onConnectionTested,
        )
    }
}

@Composable
private fun StatusHero(state: MainUiState, modifier: Modifier = Modifier) {
    val colors = statusColors(state.status)
    Card(
        modifier = modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = colors.first),
        shape = RoundedCornerShape(28.dp),
    ) {
        Column(
            modifier = Modifier.padding(24.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(
                text = state.status.displayName(),
                style = MaterialTheme.typography.headlineMedium,
                color = colors.second,
                fontWeight = FontWeight.Bold,
            )
            Text(
                text = state.statusMessage,
                style = MaterialTheme.typography.bodyLarge,
                color = colors.second,
            )
        }
    }
}

@Composable
private fun ConnectionCard(state: MainUiState, modifier: Modifier = Modifier) {
    Card(modifier = modifier.fillMaxWidth()) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text("设备状态", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
            StatusRow("手机 IMU", if (state.sensorAvailable) "可用" else "不可用", state.sensorAvailable)
            StatusRow("Orin 局域网", if (state.orinConnected) "已连接" else "未连接", state.orinConnected)
        }
    }
}

@Composable
private fun StatusRow(label: String, value: String, healthy: Boolean, modifier: Modifier = Modifier) {
    Row(
        modifier = modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(label, style = MaterialTheme.typography.bodyLarge)
        Text(
            text = value,
            style = MaterialTheme.typography.labelLarge,
            color = if (healthy) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.error,
            fontWeight = FontWeight.Bold,
        )
    }
}

@Composable
private fun RealModeCard(
    state: MainUiState,
    onGuardianToggle: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Card(modifier = modifier.fillMaxWidth()) {
        Column(
            modifier = Modifier.padding(20.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            Text("真实跌倒检测", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
            Text(
                "手机请放在裤兜或腰部。开始后会在后台持续读取加速度计和陀螺仪。",
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Button(
                onClick = onGuardianToggle,
                enabled = state.sensorAvailable && state.incident == null,
                modifier = Modifier.fillMaxWidth().height(56.dp),
            ) {
                Text(if (state.guarding) "停止守护" else "开始守护")
            }
        }
    }
}

@Composable
private fun DemoModeCard(
    state: MainUiState,
    onDemoRequested: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Card(modifier = modifier.fillMaxWidth()) {
        Column(
            modifier = Modifier.padding(20.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            Text("安全演示", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
            Text(
                "确认后倒计时 5 秒。请缓慢、安全地趴下，不需要真的摔倒。",
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Button(
                onClick = onDemoRequested,
                enabled = state.orinConnected && state.incident == null,
                modifier = Modifier.fillMaxWidth().height(64.dp),
            ) {
                Text("模拟跌倒", style = MaterialTheme.typography.titleMedium)
            }
        }
    }
}

@Composable
private fun IncidentCard(
    incident: IncidentUiState,
    onCancel: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val terminal = incident.stage in setOf(
        EventStage.NOTIFIED,
        EventStage.NOT_FALL,
        EventStage.CANCELLED,
        EventStage.FAILED,
    )
    Card(
        modifier = modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.secondaryContainer),
    ) {
        Column(
            modifier = Modifier.padding(20.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text("当前事件", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
            Text(incident.stage.displayName(), style = MaterialTheme.typography.titleMedium)
            Text(incident.message, color = MaterialTheme.colorScheme.onSecondaryContainer)
            if (!terminal && incident.cancelSecondsRemaining > 0) {
                Text("${incident.cancelSecondsRemaining} 秒内可取消联系人通知")
                OutlinedButton(
                    onClick = onCancel,
                    modifier = Modifier.fillMaxWidth().height(56.dp),
                ) {
                    Text("我没事，取消告警")
                }
            }
        }
    }
}

@Composable
private fun DemoConfirmationDialog(
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
) {
    var acknowledged by rememberSaveable { mutableStateOf(false) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("确认触发演示？") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text("这将执行真实通知流程。视觉确认跌倒后，紧急联系人收到的消息不会标记为演示。")
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Checkbox(checked = acknowledged, onCheckedChange = { acknowledged = it })
                    Text("我确认已告知现场人员和紧急联系人")
                }
            }
        },
        confirmButton = {
            Button(onClick = onConfirm, enabled = acknowledged) { Text("开始 5 秒倒计时") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("返回") } },
    )
}

@Composable
private fun CountdownDialog(
    seconds: Int,
    onCancel: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = {},
        title = { Text("请安全就位") },
        text = {
            Column(
                modifier = Modifier.fillMaxWidth(),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                Text(
                    text = seconds.toString(),
                    style = MaterialTheme.typography.displayLarge,
                    fontWeight = FontWeight.Bold,
                    color = MaterialTheme.colorScheme.primary,
                )
                Text("倒计时结束后将触发视觉确认")
            }
        },
        confirmButton = {},
        dismissButton = { OutlinedButton(onClick = onCancel) { Text("取消演示") } },
    )
}

@Composable
private fun SettingsDialog(
    initialSettings: AppSettings,
    testingConnection: Boolean,
    onDismiss: () -> Unit,
    onSave: (AppSettings) -> Unit,
    onTestConnection: (AppSettings) -> Unit,
) {
    var settings by remember(initialSettings) { mutableStateOf(initialSettings) }
    var advancedVisible by rememberSaveable { mutableStateOf(false) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("设置") },
        text = {
            Column(
                modifier = Modifier.verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                OutlinedTextField(
                    value = settings.orinBaseUrl,
                    onValueChange = { settings = settings.copy(orinBaseUrl = it) },
                    label = { Text("Orin 局域网地址") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = settings.deviceName,
                    onValueChange = { settings = settings.copy(deviceName = it) },
                    label = { Text("设备名称") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = settings.sharedToken,
                    onValueChange = { settings = settings.copy(sharedToken = it) },
                    label = { Text("共享 Token") },
                    visualTransformation = PasswordVisualTransformation(),
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text("本地模拟后端")
                        Text(
                            "关闭后连接真实 Orin",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    Switch(
                        checked = settings.simulationEnabled,
                        onCheckedChange = { settings = settings.copy(simulationEnabled = it) },
                    )
                }
                OutlinedButton(
                    onClick = { onTestConnection(settings) },
                    enabled = !testingConnection,
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    if (testingConnection) {
                        CircularProgressIndicator(modifier = Modifier.height(20.dp), strokeWidth = 2.dp)
                    } else {
                        Text("测试连接")
                    }
                }
                HorizontalDivider()
                TextButton(onClick = { advancedVisible = !advancedVisible }) {
                    Text(if (advancedVisible) "收起高级设置" else "展开高级设置")
                }
                if (advancedVisible) {
                    ThresholdField("冲击阈值 (g)", settings.thresholds.impactG) {
                        settings = settings.copy(thresholds = settings.thresholds.copy(impactG = it))
                    }
                    ThresholdField("失重阈值 (g)", settings.thresholds.freeFallG) {
                        settings = settings.copy(thresholds = settings.thresholds.copy(freeFallG = it))
                    }
                    ThresholdField("姿态变化 (°)", settings.thresholds.orientationChangeDeg) {
                        settings = settings.copy(thresholds = settings.thresholds.copy(orientationChangeDeg = it))
                    }
                    ThresholdField("静止时间 (秒)", settings.thresholds.stillnessSeconds) {
                        settings = settings.copy(thresholds = settings.thresholds.copy(stillnessSeconds = it))
                    }
                }
            }
        },
        confirmButton = { Button(onClick = { onSave(settings) }) { Text("保存") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("取消") } },
    )
}

@Composable
private fun ThresholdField(
    label: String,
    value: Float,
    modifier: Modifier = Modifier,
    onValueChanged: (Float) -> Unit,
) {
    OutlinedTextField(
        value = value.toString(),
        onValueChange = { text -> text.toFloatOrNull()?.let(onValueChanged) },
        label = { Text(label) },
        singleLine = true,
        modifier = modifier.fillMaxWidth(),
    )
}

@Composable
private fun statusColors(status: GuardianStatus): Pair<Color, Color> = when (status) {
    GuardianStatus.NOTIFIED, GuardianStatus.FAILED ->
        MaterialTheme.colorScheme.errorContainer to MaterialTheme.colorScheme.onErrorContainer
    GuardianStatus.SUSPECTED_FALL, GuardianStatus.VERIFYING ->
        MaterialTheme.colorScheme.tertiaryContainer to MaterialTheme.colorScheme.onTertiaryContainer
    GuardianStatus.GUARDING ->
        MaterialTheme.colorScheme.primaryContainer to MaterialTheme.colorScheme.onPrimaryContainer
    else -> MaterialTheme.colorScheme.surfaceVariant to MaterialTheme.colorScheme.onSurfaceVariant
}

private fun GuardianStatus.displayName(): String = when (this) {
    GuardianStatus.DISCONNECTED -> "未连接"
    GuardianStatus.IDLE -> "待机"
    GuardianStatus.GUARDING -> "守护中"
    GuardianStatus.SUSPECTED_FALL -> "疑似跌倒"
    GuardianStatus.VERIFYING -> "机器人确认中"
    GuardianStatus.NOTIFIED -> "已通知"
    GuardianStatus.NOT_FALL -> "未发现跌倒"
    GuardianStatus.CANCELLED -> "已取消"
    GuardianStatus.FAILED -> "处理失败"
}

private fun EventStage.displayName(): String = when (this) {
    EventStage.ACCEPTED -> "Orin 已接收"
    EventStage.SCANNING -> "正在扫描"
    EventStage.VERIFYING -> "视觉研判"
    EventStage.NOTIFIED -> "已通知联系人"
    EventStage.NOT_FALL -> "未发现跌倒"
    EventStage.CANCELLED -> "已取消"
    EventStage.FAILED -> "处理失败"
}

@Preview(showBackground = true, widthDp = 390, heightDp = 844)
@Composable
private fun MainScreenPreview() {
    FallGuardTheme(darkTheme = false) {
        MainScreen(
            state = MainUiState(orinConnected = true, statusMessage = "本地模拟已就绪"),
            onModeChanged = {},
            onGuardianToggle = {},
            onDemoRequested = {},
            onDemoConfirmed = {},
            onDemoDismissed = {},
            onDemoCountdownCancelled = {},
            onIncidentCancelled = {},
            onSettingsRequested = {},
            onSettingsDismissed = {},
            onSettingsSaved = {},
            onConnectionTested = {},
        )
    }
}
