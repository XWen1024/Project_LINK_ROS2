package com.projectlink.fallguard

enum class AppMode {
    REAL,
    DEMO,
}

enum class GuardianStatus {
    DISCONNECTED,
    IDLE,
    GUARDING,
    SUSPECTED_FALL,
    VERIFYING,
    NOTIFIED,
    NOT_FALL,
    CANCELLED,
    FAILED,
}

enum class EventStage {
    ACCEPTED,
    SCANNING,
    VERIFYING,
    NOTIFIED,
    NOT_FALL,
    CANCELLED,
    FAILED,
}

data class ImuThresholds(
    val impactG: Float = 2.5f,
    val freeFallG: Float = 0.65f,
    val orientationChangeDeg: Float = 55f,
    val stillnessSeconds: Float = 2f,
)

data class AppSettings(
    val orinBaseUrl: String = "http://192.168.1.100:8765",
    val deviceName: String = "demo-phone",
    val sharedToken: String = "",
    val simulationEnabled: Boolean = true,
    val thresholds: ImuThresholds = ImuThresholds(),
)

data class IncidentUiState(
    val eventId: String,
    val stage: EventStage,
    val cancelSecondsRemaining: Int,
    val message: String,
)

data class ImuSummary(
    val peakAccelG: Float,
    val orientationChangeDeg: Float,
    val inactivityMs: Long,
)

data class FallEventRequest(
    val eventId: String,
    val mode: AppMode,
    val occurredAtMs: Long,
    val deviceName: String,
    val cancelWindowMs: Long = 15_000,
    val imu: ImuSummary?,
)

data class ServiceRuntimeState(
    val guarding: Boolean = false,
    val sensorAvailable: Boolean = true,
    val orinConnected: Boolean = false,
    val status: GuardianStatus = GuardianStatus.IDLE,
    val statusMessage: String = "准备就绪",
    val incident: IncidentUiState? = null,
)

data class MainUiState(
    val mode: AppMode = AppMode.REAL,
    val status: GuardianStatus = GuardianStatus.IDLE,
    val statusMessage: String = "准备就绪",
    val guarding: Boolean = false,
    val sensorAvailable: Boolean = true,
    val orinConnected: Boolean = false,
    val testingConnection: Boolean = false,
    val settings: AppSettings = AppSettings(),
    val settingsVisible: Boolean = false,
    val demoConfirmationVisible: Boolean = false,
    val demoCountdown: Int? = null,
    val incident: IncidentUiState? = null,
)

