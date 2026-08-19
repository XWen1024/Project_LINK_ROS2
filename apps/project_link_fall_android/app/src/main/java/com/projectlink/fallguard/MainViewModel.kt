package com.projectlink.fallguard

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import java.util.UUID
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

class MainViewModel(application: Application) : AndroidViewModel(application) {
    private val settingsStore = SettingsStore(application)
    private val _uiState = MutableStateFlow(MainUiState())
    val uiState: StateFlow<MainUiState> = _uiState.asStateFlow()
    private var demoJob: Job? = null
    private var activeDemoGateway: FallGateway? = null
    private var activeDemoEventId: String? = null

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
                it.copy(status = GuardianStatus.DISCONNECTED, statusMessage = "请先连接 Orin")
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
        demoJob?.cancel()
        viewModelScope.launch {
            runCatching { activeDemoGateway?.cancel(incident.eventId) }
            publishDemoStage(EventStage.CANCELLED, 0, "已在通知联系人前取消")
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
        _uiState.update { it.copy(settingsVisible = true) }
    }

    fun hideSettings() {
        _uiState.update { it.copy(settingsVisible = false) }
    }

    fun saveSettings(settings: AppSettings) {
        viewModelScope.launch {
            settingsStore.save(settings)
            _uiState.update {
                it.copy(
                    settingsVisible = false,
                    orinConnected = if (settings.simulationEnabled) true else false,
                    statusMessage = if (settings.simulationEnabled) "本地模拟已就绪" else "请测试 Orin 连接",
                )
            }
        }
    }

    fun testConnection(settings: AppSettings) {
        _uiState.update { it.copy(testingConnection = true) }
        viewModelScope.launch {
            val connected = FallGatewayFactory.create(settings).health()
            _uiState.update {
                it.copy(
                    testingConnection = false,
                    orinConnected = connected,
                    status = if (connected) GuardianStatus.IDLE else GuardianStatus.DISCONNECTED,
                    statusMessage = if (connected) "Orin 连接正常" else "无法连接 Orin，请检查地址和 Token",
                )
            }
        }
    }

    private suspend fun startDemoIncident() {
        val settings = _uiState.value.settings
        val eventId = UUID.randomUUID().toString()
        val gateway = FallGatewayFactory.create(settings)
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
