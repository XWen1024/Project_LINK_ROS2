package com.projectlink.fallguard

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import java.util.UUID
import kotlin.math.sqrt
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

class FallDetectionService : Service(), SensorEventListener {
    private val serviceScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private lateinit var sensorManager: SensorManager
    private lateinit var settingsStore: SettingsStore
    private var detector: FallDetector? = null
    private var latestGyroDegPerSecond = 0f
    private var latestPitchDeg: Float? = null
    private var latestRollDeg: Float? = null
    private var incidentJob: Job? = null
    private var activeGateway: FallGateway? = null
    private var activeEventId: String? = null

    override fun onCreate() {
        super.onCreate()
        settingsStore = SettingsStore(applicationContext)
        sensorManager = getSystemService(SensorManager::class.java)
        createNotificationChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> stopGuardian()
            ACTION_CANCEL_EVENT -> cancelEvent(intent.getStringExtra(EXTRA_EVENT_ID))
            ACTION_CLEAR_EVENT -> clearEvent()
            else -> startGuardian()
        }
        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        sensorManager.unregisterListener(this)
        serviceScope.cancel()
        _runtimeState.update { it.copy(guarding = false) }
        super.onDestroy()
    }

    override fun onSensorChanged(event: SensorEvent) {
        when (event.sensor.type) {
            Sensor.TYPE_GYROSCOPE -> {
                val radiansPerSecond = magnitude(event.values)
                latestGyroDegPerSecond = Math.toDegrees(radiansPerSecond.toDouble()).toFloat()
            }
            Sensor.TYPE_ROTATION_VECTOR -> updateOrientation(event.values)
            Sensor.TYPE_ACCELEROMETER -> {
                val accelerationG = magnitude(event.values) / SensorManager.GRAVITY_EARTH
                val detection = detector?.onFrame(
                    SensorFrame(
                        timestampMs = event.timestamp / 1_000_000,
                        accelerationG = accelerationG,
                        angularVelocityDegPerSecond = latestGyroDegPerSecond,
                        pitchDeg = latestPitchDeg,
                        rollDeg = latestRollDeg,
                    ),
                )
                if (detection != null && incidentJob?.isActive != true) {
                    incidentJob = serviceScope.launch { submitDetectedFall(detection) }
                }
            }
        }
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) = Unit

    private fun startGuardian() {
        ServiceCompat.startForeground(
            this,
            NOTIFICATION_ID,
            buildNotification("正在监测手机 IMU"),
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
                ServiceInfo.FOREGROUND_SERVICE_TYPE_HEALTH
            } else {
                0
            },
        )
        serviceScope.launch {
            val settings = settingsStore.settings.first()
            detector = FallDetector(settings.thresholds)
            activeGateway = FallGatewayFactory.create(settings)
            val accelerometer = sensorManager.getDefaultSensor(Sensor.TYPE_ACCELEROMETER)
            val gyroscope = sensorManager.getDefaultSensor(Sensor.TYPE_GYROSCOPE)
            val rotationVector = sensorManager.getDefaultSensor(Sensor.TYPE_ROTATION_VECTOR)
            val available = accelerometer != null && gyroscope != null
            if (!available) {
                _runtimeState.value = ServiceRuntimeState(
                    guarding = false,
                    sensorAvailable = false,
                    status = GuardianStatus.FAILED,
                    statusMessage = "手机缺少加速度计或陀螺仪",
                )
                stopSelf()
                return@launch
            }
            sensorManager.registerListener(this@FallDetectionService, accelerometer, SENSOR_PERIOD_US)
            sensorManager.registerListener(this@FallDetectionService, gyroscope, SENSOR_PERIOD_US)
            rotationVector?.let {
                sensorManager.registerListener(this@FallDetectionService, it, SENSOR_PERIOD_US)
            }
            val connected = activeGateway?.health() == true
            _runtimeState.value = ServiceRuntimeState(
                guarding = true,
                sensorAvailable = true,
                orinConnected = connected,
                status = GuardianStatus.GUARDING,
                statusMessage = if (connected) "正在监测手机 IMU" else "守护中，Orin 暂未连接",
            )
        }
    }

    private fun stopGuardian() {
        sensorManager.unregisterListener(this)
        detector?.reset()
        detector = null
        _runtimeState.value = ServiceRuntimeState(statusMessage = "守护已停止")
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    private suspend fun submitDetectedFall(detection: FallDetection) {
        val settings = settingsStore.settings.first()
        val eventId = UUID.randomUUID().toString()
        activeEventId = eventId
        val request = FallEventRequest(
            eventId = eventId,
            mode = AppMode.REAL,
            occurredAtMs = System.currentTimeMillis(),
            deviceName = settings.deviceName,
            imu = detection.summary,
        )
        val deadline = System.currentTimeMillis() + RETRY_WINDOW_MS
        var submitted = false
        while (!submitted && System.currentTimeMillis() < deadline) {
            submitted = runCatching {
                activeGateway?.submit(request)
                true
            }.getOrDefault(false)
            if (!submitted) delay(2_000)
        }
        if (!submitted) {
            publishStage(eventId, EventStage.FAILED, 0, "60 秒内无法连接 Orin")
            return
        }

        val cancelDeadline = System.currentTimeMillis() + CANCEL_WINDOW_MS
        var connectionFailureDeadline = System.currentTimeMillis() + RETRY_WINDOW_MS
        publishStage(eventId, EventStage.ACCEPTED, 15, "Orin 已接收，正在启动视觉扫描")
        while (true) {
            val remaining = ((cancelDeadline - System.currentTimeMillis() + 999) / 1_000)
                .toInt()
                .coerceAtLeast(0)
            val stage = runCatching { activeGateway?.status(eventId) ?: EventStage.FAILED }
                .getOrElse {
                    if (System.currentTimeMillis() >= connectionFailureDeadline) {
                        publishStage(eventId, EventStage.FAILED, 0, "Orin 状态查询连续失败 60 秒")
                        break
                    }
                    _runtimeState.update { state ->
                        state.copy(orinConnected = false, statusMessage = "Orin 连接中断，正在重试")
                    }
                    delay(1_000)
                    continue
                }
            connectionFailureDeadline = System.currentTimeMillis() + RETRY_WINDOW_MS
            publishStage(eventId, stage, remaining, stageMessage(stage))
            if (stage.isTerminal()) break
            delay(1_000)
        }
    }

    private fun cancelEvent(eventId: String?) {
        if (eventId == null || eventId != activeEventId) return
        serviceScope.launch {
            val result = runCatching { activeGateway?.cancel(eventId) ?: error("Orin 客户端不可用") }
            result.onSuccess { stage ->
                if (stage == EventStage.CANCELLED) {
                    incidentJob?.cancel()
                    publishStage(eventId, stage, 0, "已在通知联系人前取消")
                } else {
                    publishStage(eventId, stage, 0, stageMessage(stage))
                }
            }.onFailure { error ->
                val current = _runtimeState.value.incident?.stage ?: EventStage.ACCEPTED
                publishStage(eventId, current, 0, "取消未获 Orin 确认：${error.message ?: "网络错误"}")
            }
        }
    }

    private fun clearEvent() {
        activeEventId = null
        _runtimeState.update { state ->
            state.copy(
                status = if (state.guarding) GuardianStatus.GUARDING else GuardianStatus.IDLE,
                statusMessage = if (state.guarding) "正在监测手机 IMU" else "准备就绪",
                incident = null,
            )
        }
    }

    private fun publishStage(eventId: String, stage: EventStage, seconds: Int, message: String) {
        _runtimeState.update { current ->
            current.copy(
                guarding = true,
                orinConnected = stage != EventStage.FAILED,
                status = stage.guardianStatus(),
                statusMessage = message,
                incident = IncidentUiState(eventId, stage, seconds, message),
            )
        }
        getSystemService(NotificationManager::class.java).notify(NOTIFICATION_ID, buildNotification(message))
    }

    private fun updateOrientation(rotationVector: FloatArray) {
        val rotationMatrix = FloatArray(9)
        val orientation = FloatArray(3)
        SensorManager.getRotationMatrixFromVector(rotationMatrix, rotationVector)
        SensorManager.getOrientation(rotationMatrix, orientation)
        latestPitchDeg = Math.toDegrees(orientation[1].toDouble()).toFloat()
        latestRollDeg = Math.toDegrees(orientation[2].toDouble()).toFloat()
    }

    private fun magnitude(values: FloatArray): Float = sqrt(
        values[0] * values[0] + values[1] * values[1] + values[2] * values[2],
    )

    private fun createNotificationChannel() {
        val channel = NotificationChannel(
            NOTIFICATION_CHANNEL_ID,
            "跌倒守护",
            NotificationManager.IMPORTANCE_LOW,
        ).apply { description = "保持手机 IMU 跌倒检测在后台运行" }
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    private fun buildNotification(message: String): Notification {
        val activityIntent = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        return NotificationCompat.Builder(this, NOTIFICATION_CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_dialog_alert)
            .setContentTitle("LINK 跌倒守护")
            .setContentText(message)
            .setContentIntent(activityIntent)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .build()
    }

    companion object {
        const val ACTION_START = "com.projectlink.fallguard.action.START"
        const val ACTION_STOP = "com.projectlink.fallguard.action.STOP"
        const val ACTION_CANCEL_EVENT = "com.projectlink.fallguard.action.CANCEL_EVENT"
        const val ACTION_CLEAR_EVENT = "com.projectlink.fallguard.action.CLEAR_EVENT"
        const val EXTRA_EVENT_ID = "event_id"
        private const val NOTIFICATION_CHANNEL_ID = "fall_guard_service"
        private const val NOTIFICATION_ID = 1001
        private const val SENSOR_PERIOD_US = 20_000
        private const val RETRY_WINDOW_MS = 60_000L
        private const val CANCEL_WINDOW_MS = 15_000L

        private val _runtimeState = MutableStateFlow(ServiceRuntimeState())
        val runtimeState: StateFlow<ServiceRuntimeState> = _runtimeState.asStateFlow()

        fun start(context: Context) {
            androidx.core.content.ContextCompat.startForegroundService(
                context,
                Intent(context, FallDetectionService::class.java).setAction(ACTION_START),
            )
        }

        fun stop(context: Context) {
            context.startService(Intent(context, FallDetectionService::class.java).setAction(ACTION_STOP))
        }

        fun cancel(context: Context, eventId: String) {
            context.startService(
                Intent(context, FallDetectionService::class.java)
                    .setAction(ACTION_CANCEL_EVENT)
                    .putExtra(EXTRA_EVENT_ID, eventId),
            )
        }

        fun clear(context: Context) {
            context.startService(Intent(context, FallDetectionService::class.java).setAction(ACTION_CLEAR_EVENT))
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

private fun stageMessage(stage: EventStage): String = when (stage) {
    EventStage.ACCEPTED -> "Orin 已接收事件"
    EventStage.SCANNING -> "机器人正在扫描现场"
    EventStage.VERIFYING -> "正在进行视觉二次研判"
    EventStage.NOTIFIED -> "已通知紧急联系人"
    EventStage.NOT_FALL -> "视觉判断未发现跌倒"
    EventStage.CANCELLED -> "告警已取消"
    EventStage.FAILED -> "Orin 处理失败"
}
