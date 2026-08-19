package com.projectlink.fallguard

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import java.util.UUID
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

    init {
        viewModelScope.launch {
            settingsStore.settings.collect { settings ->
                _uiState.update {
                    it.copy(
                        settings = settings,
                        orinConnected = settings.simulationEnabled,
                        status = if (settings.simulationEnabled) GuardianStatus.IDLE else it.status,
                        statusMessage = if (settings.simulationEnabled) "本地模拟已就绪" else it.statusMessage,
                    )
                }
            }
        }
    }

    fun setMode(mode: AppMode) {
        if (_uiState.value.incident == null && _uiState.value.demoCountdown == null) {
            _uiState.update { it.copy(mode = mode) }
        }
    }

    fun toggleGuardian() {
        _uiState.update { state ->
            val guarding = !state.guarding
            state.copy(
                guarding = guarding,
                status = if (guarding) GuardianStatus.GUARDING else GuardianStatus.IDLE,
                statusMessage = if (guarding) "正在监测手机 IMU" else "守护已停止",
            )
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
            startFakeIncident()
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
        demoJob?.cancel()
        _uiState.update {
            it.copy(
                status = GuardianStatus.CANCELLED,
                statusMessage = "告警已取消",
                incident = it.incident?.copy(
                    stage = EventStage.CANCELLED,
                    cancelSecondsRemaining = 0,
                    message = "已在通知联系人前取消",
                ),
            )
        }
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
            _uiState.update { it.copy(settingsVisible = false) }
        }
    }

    fun testConnection(settings: AppSettings) {
        _uiState.update { it.copy(testingConnection = true) }
        viewModelScope.launch {
            delay(500)
            val connected = settings.simulationEnabled
            _uiState.update {
                it.copy(
                    testingConnection = false,
                    orinConnected = connected,
                    status = if (connected) GuardianStatus.IDLE else GuardianStatus.DISCONNECTED,
                    statusMessage = if (connected) "本地模拟连接正常" else "真实网络将在接口接入后测试",
                )
            }
        }
    }

    private suspend fun startFakeIncident() {
        val eventId = UUID.randomUUID().toString()
        _uiState.update {
            it.copy(
                demoCountdown = null,
                status = GuardianStatus.SUSPECTED_FALL,
                statusMessage = "已触发疑似跌倒",
                incident = IncidentUiState(eventId, EventStage.ACCEPTED, 15, "Orin 已接收事件"),
            )
        }
        val stages = listOf(
            Triple(EventStage.SCANNING, GuardianStatus.VERIFYING, "机器人正在扫描现场"),
            Triple(EventStage.VERIFYING, GuardianStatus.VERIFYING, "正在进行视觉二次研判"),
            Triple(EventStage.NOTIFIED, GuardianStatus.NOTIFIED, "已通知紧急联系人"),
        )
        var remaining = 15
        for ((stage, status, message) in stages) {
            repeat(3) {
                delay(1_000)
                remaining = (remaining - 1).coerceAtLeast(0)
                _uiState.update { state ->
                    state.copy(incident = state.incident?.copy(cancelSecondsRemaining = remaining))
                }
            }
            _uiState.update { state ->
                state.copy(
                    status = status,
                    statusMessage = message,
                    incident = state.incident?.copy(stage = stage, message = message),
                )
            }
        }
    }
}
