package com.projectlink.fallguard

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import java.util.UUID
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

class MainViewModel(application: Application) : AndroidViewModel(application) {
    private val settingsStore = SettingsStore(application)
    private val _uiState = MutableStateFlow(MainUiState())
    val uiState: StateFlow<MainUiState> = _uiState.asStateFlow()
    private var demoJob: Job? = null
    private var activeDemoGateway: FallGateway? = null
    private var activeDemoEventId: String? = null
    private var heartbeatJob: Job? = null
    private val healthCheckMutex = Mutex()

    init {
        viewModelScope.launch {
            settingsStore.settings.collect { settings ->
                _uiState.update { state ->
                    state.copy(
                        settings = settings,
                        orinConnected = if (settings.simulationEnabled) true else state.orinConnected,
                        statusMessage = if (settings.simulationEnabled && state.status == GuardianStatus.IDLE) {
                            "本地模拟已就绪"
                        } else {
                            state.statusMessage
                        },
                    )
                }
            }
        }
        viewModelScope.launch {
            FallDetectionService.runtimeState.collect { runtime ->
                val relevant = runtime.guarding || runtime.incident != null || _uiState.value.guarding
                if (relevant) {
                    _uiState.update { state ->
                        state.copy(
                            guarding = runtime.guarding,
                            sensorAvailable = runtime.sensorAvailable,
                            orinConnected = runtime.orinConnected,
                            status = runtime.status,
                            statusMessage = runtime.statusMessage,
                            incident = runtime.incident ?: state.incident,
                        )
                    }
                }
            }
        }
    }

    fun setMode(mode: AppMode) {
        if (_uiState.value.incident == null && _uiState.value.demoCountdown == null && !_uiState.value.guarding) {
            _uiState.update { it.copy(mode = mode) }
        }
    }

    fun toggleGuardian() {
        val context = getApplication<Application>()
        if (_uiState.value.guarding) {
            FallDetectionService.stop(context)
            _uiState.update {
                it.copy(guarding = false, status = GuardianStatus.IDLE, statusMessage = "守护已停止")
            }
        } else {
            FallDetectionService.start(context)
            _uiState.update {
                it.copy(guarding = true, status = GuardianStatus.GUARDING, statusMessage = "正在启动守护服务")
            }
        }
    }

    fun requestDemo() {
        if (_uiState.value.orinConnected) {
            _uiState.update { it.copy(demoConfirmationVisible = true) }
        } else {
            _uiState.update {
                it.copy(
                    status = GuardianStatus.DISCONNECTED,
                    statusMessage = "请先连接 Orin",
                    informationDialog = InformationDialogState(
                        title = "Orin 尚未连接",
                        message = "请在设置中确认 Orin 局域网地址和 Token。保存后 App 会自动检查连接。",
                        details = "当前配置：${it.settings.orinBaseUrl}\n手机不能使用 127.0.0.1；也可在设置中用“测试连接”单独检查尚未保存的内容。",
                        isError = true,
                    ),
                )
            }
        }
    }

    fun confirmDemo() {
        demoJob?.cancel()
        demoJob = viewModelScope.launch {
            _uiState.update { it.copy(demoConfirmationVisible = false, demoCountdown = 5) }
            for (second in 5 downTo 1) {
                _uiState.update { it.copy(demoCountdown = second) }
                delay(1_000)
            }
            startDemoIncident()
        }
    }

    fun dismissDemo() {
        _uiState.update { it.copy(demoConfirmationVisible = false) }
    }

    fun cancelDemoCountdown() {
        demoJob?.cancel()
        _uiState.update { it.copy(demoCountdown = null, statusMessage = "演示已取消") }
    }

    fun cancelIncident() {
        val incident = _uiState.value.incident ?: return
        if (_uiState.value.mode == AppMode.REAL) {
            FallDetectionService.cancel(getApplication(), incident.eventId)
            return
        }
        viewModelScope.launch {
            val result = runCatching { activeDemoGateway?.cancel(incident.eventId) ?: error("Orin 客户端不可用") }
            result.onSuccess { stage ->
                if (stage == EventStage.CANCELLED) {
                    demoJob?.cancel()
                    publishDemoStage(stage, 0, "已在通知联系人前取消")
                } else {
                    publishDemoStage(stage, 0, stage.message())
                }
            }.onFailure { error ->
                publishDemoStage(
                    incident.stage,
                    incident.cancelSecondsRemaining,
                    "取消未获 Orin 确认：${error.message ?: "网络错误"}",
                )
                showInformation(
                    title = "取消请求失败",
                    message = "Orin 没有确认取消，联系人通知状态仍不确定。",
                    details = diagnosticDetails(error),
                    isError = true,
                )
            }
        }
    }

    fun clearIncident() {
        if (_uiState.value.guarding) {
            FallDetectionService.clear(getApplication())
        }
        _uiState.update { state ->
            val nextStatus = if (state.guarding) GuardianStatus.GUARDING else GuardianStatus.IDLE
            state.copy(
                status = nextStatus,
                statusMessage = if (state.guarding) "正在监测手机 IMU" else "准备就绪",
                incident = null,
            )
        }
        activeDemoEventId = null
        activeDemoGateway = null
    }

    fun showSettings() {
        _uiState.update { it.copy(settingsVisible = true, settingsDraft = it.settings) }
    }

    fun hideSettings() {
        _uiState.update { it.copy(settingsVisible = false, settingsDraft = null) }
    }

    fun startConnectionMonitoring() {
        if (heartbeatJob?.isActive == true) return
        heartbeatJob = viewModelScope.launch {
            runAutomaticHealthCheck()
            while (isActive) {
                delay(HEARTBEAT_INTERVAL_MS)
                runAutomaticHealthCheck()
            }
        }
    }

    fun stopConnectionMonitoring() {
        heartbeatJob?.cancel()
        heartbeatJob = null
    }

    fun dismissInformation() {
        _uiState.update { state ->
            state.copy(
                informationDialog = null,
                settingsVisible = state.informationDialog?.returnToSettings == true,
            )
        }
    }

    fun saveSettings(settings: AppSettings) {
        viewModelScope.launch {
            settingsStore.save(settings)
            _uiState.update {
                it.copy(
                    settings = settings,
                    settingsDraft = null,
                    settingsVisible = false,
                    statusMessage = "设置已保存，正在检查后端连接",
                )
            }
            performHealthCheck(
                settings = settings,
                showDialog = true,
                returnToSettings = false,
                manual = false,
                applyConnectionState = true,
            )
        }
    }

    fun testConnection(settings: AppSettings) {
        _uiState.update { it.copy(settingsDraft = settings, testingConnection = true) }
        viewModelScope.launch {
            performHealthCheck(
                settings = settings,
                showDialog = true,
                returnToSettings = true,
                manual = true,
                applyConnectionState = false,
            )
        }
    }

    private suspend fun runAutomaticHealthCheck() {
        val settings = settingsStore.settings.first()
        performHealthCheck(
            settings = settings,
            showDialog = false,
            returnToSettings = false,
            manual = false,
            applyConnectionState = true,
        )
    }

    private suspend fun performHealthCheck(
        settings: AppSettings,
        showDialog: Boolean,
        returnToSettings: Boolean,
        manual: Boolean,
        applyConnectionState: Boolean,
    ) {
        healthCheckMutex.withLock {
            if (manual) {
                _uiState.update { it.copy(testingConnection = true) }
            }
            val result = FallGatewayFactory.create(settings, getApplication()).health()
            _uiState.update { state ->
                val mayReplaceMainStatus = state.incident == null && !state.guarding
                state.copy(
                    testingConnection = if (manual) false else state.testingConnection,
                    settingsVisible = if (manual) false else state.settingsVisible,
                    orinConnected = if (applyConnectionState) result.success else state.orinConnected,
                    status = if (applyConnectionState && mayReplaceMainStatus) {
                        if (result.success) GuardianStatus.IDLE else GuardianStatus.DISCONNECTED
                    } else {
                        state.status
                    },
                    statusMessage = if (applyConnectionState && mayReplaceMainStatus) {
                        result.summary
                    } else {
                        state.statusMessage
                    },
                    informationDialog = if (showDialog) {
                        InformationDialogState(
                            title = if (result.success) "连接成功" else "连接失败",
                            message = result.summary,
                            details = result.details,
                            isError = !result.success,
                            returnToSettings = returnToSettings,
                        )
                    } else {
                        state.informationDialog
                    },
                )
            }
        }
    }

    private suspend fun startDemoIncident() {
        val settings = _uiState.value.settings
        val eventId = UUID.randomUUID().toString()
        val gateway = FallGatewayFactory.create(settings, getApplication())
        activeDemoGateway = gateway
        activeDemoEventId = eventId
        _uiState.update {
            it.copy(
                demoCountdown = null,
                status = GuardianStatus.SUSPECTED_FALL,
                statusMessage = "正在向 Orin 提交事件",
                incident = IncidentUiState(eventId, EventStage.ACCEPTED, 15, "正在提交事件"),
            )
        }
        try {
            gateway.submit(
                FallEventRequest(
                    eventId = eventId,
                    mode = AppMode.DEMO,
                    occurredAtMs = System.currentTimeMillis(),
                    deviceName = settings.deviceName,
                    imu = null,
                ),
            )
            val cancelDeadline = System.currentTimeMillis() + 15_000
            while (true) {
                val remaining = ((cancelDeadline - System.currentTimeMillis() + 999) / 1_000)
                    .toInt()
                    .coerceAtLeast(0)
                val stage = gateway.status(eventId)
                publishDemoStage(stage, remaining, stage.message())
                if (stage.isTerminal()) break
                delay(1_000)
            }
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (error: Exception) {
            publishDemoStage(EventStage.FAILED, 0, error.message ?: "Orin 连接失败")
            showInformation(
                title = "事件提交失败",
                message = "App 未能完成 Orin 跌倒事件请求。",
                details = diagnosticDetails(error),
                isError = true,
            )
        }
    }

    private fun publishDemoStage(stage: EventStage, remaining: Int, message: String) {
        val eventId = activeDemoEventId ?: return
        _uiState.update {
            it.copy(
                status = stage.guardianStatus(),
                statusMessage = message,
                incident = IncidentUiState(eventId, stage, remaining, message),
            )
        }
    }

    private fun showInformation(
        title: String,
        message: String,
        details: String,
        isError: Boolean,
    ) {
        _uiState.update {
            it.copy(informationDialog = InformationDialogState(title, message, details, isError))
        }
    }

    private fun diagnosticDetails(error: Throwable): String = buildString {
        appendLine("目标：${_uiState.value.settings.orinBaseUrl}")
        appendLine("当前网络：${NetworkDiagnostics.describe(getApplication())}")
        appendLine("错误类型：${error.javaClass.simpleName}")
        append("错误信息：${error.message ?: "无详细信息"}")
    }

    private companion object {
        const val HEARTBEAT_INTERVAL_MS = 15_000L
    }
}

private fun EventStage.isTerminal(): Boolean = this in setOf(
    EventStage.NOTIFIED,
    EventStage.NOT_FALL,
    EventStage.CANCELLED,
    EventStage.FAILED,
)

private fun EventStage.guardianStatus(): GuardianStatus = when (this) {
    EventStage.ACCEPTED -> GuardianStatus.SUSPECTED_FALL
    EventStage.SCANNING, EventStage.VERIFYING -> GuardianStatus.VERIFYING
    EventStage.NOTIFIED -> GuardianStatus.NOTIFIED
    EventStage.NOT_FALL -> GuardianStatus.NOT_FALL
    EventStage.CANCELLED -> GuardianStatus.CANCELLED
    EventStage.FAILED -> GuardianStatus.FAILED
}

private fun EventStage.message(): String = when (this) {
    EventStage.ACCEPTED -> "Orin 已接收事件"
    EventStage.SCANNING -> "机器人正在扫描现场"
    EventStage.VERIFYING -> "正在进行视觉二次研判"
    EventStage.NOTIFIED -> "已通知紧急联系人"
    EventStage.NOT_FALL -> "视觉判断未发现跌倒"
    EventStage.CANCELLED -> "告警已取消"
    EventStage.FAILED -> "Orin 处理失败"
}
