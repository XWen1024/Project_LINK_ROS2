package com.projectlink.fallguard

import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.nio.charset.StandardCharsets
import java.util.concurrent.ConcurrentHashMap
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import org.json.JSONObject

interface FallGateway {
    suspend fun health(): Boolean
    suspend fun submit(request: FallEventRequest): EventSnapshot
    suspend fun status(eventId: String): EventSnapshot
    suspend fun cancel(eventId: String): EventSnapshot
}
object FallGatewayFactory {
    fun create(settings: AppSettings): FallGateway = if (settings.simulationEnabled) {
        FakeOrinClient
    } else {
        RealOrinClient(settings.orinBaseUrl, settings.sharedToken)
    }
}

class RealOrinClient(
    baseUrl: String,
    private val sharedToken: String,
) : FallGateway {
    private val baseUrl = baseUrl.trim().trimEnd('/')

    override suspend fun health(): Boolean = runCatching {
        request(method = "GET", path = "/health").first in 200..299
    }.getOrDefault(false)

    override suspend fun submit(request: FallEventRequest): EventSnapshot {
        val imuJson = request.imu?.let {
            JSONObject()
                .put("peak_accel_g", it.peakAccelG.toDouble())
                .put("orientation_change_deg", it.orientationChangeDeg.toDouble())
                .put("inactivity_ms", it.inactivityMs)
        }
        val body = JSONObject()
            .put("event_id", request.eventId)
            .put("mode", request.mode.name.lowercase())
            .put("occurred_at_ms", request.occurredAtMs)
            .put("device_name", request.deviceName)
            .put("cancel_window_ms", request.cancelWindowMs)
            .put("imu", imuJson ?: JSONObject.NULL)
            .toString()
        val (_, response) = request(method = "POST", path = "/api/fall", body = body)
        return parseSnapshot(response, EventStage.ACCEPTED)
    }

    override suspend fun status(eventId: String): EventSnapshot {
        val (_, response) = request(method = "GET", path = "/api/fall/$eventId")
        return parseSnapshot(response, EventStage.ACCEPTED)
    }

    override suspend fun cancel(eventId: String): EventSnapshot {
        val (_, response) = request(method = "POST", path = "/api/fall/$eventId/cancel", body = "{}")
        return parseSnapshot(response, EventStage.CANCELLED)
    }

    private suspend fun request(
        method: String,
        path: String,
        body: String? = null,
    ): Pair<Int, String> = withContext(Dispatchers.IO) {
        require(baseUrl.startsWith("http://") || baseUrl.startsWith("https://")) {
            "Orin 地址必须以 http:// 或 https:// 开头"
        }
        val connection = (URL(baseUrl + path).openConnection() as HttpURLConnection).apply {
            requestMethod = method
            connectTimeout = 3_000
            readTimeout = 5_000
            instanceFollowRedirects = false
            setRequestProperty("Accept", "application/json")
            setRequestProperty("X-Fall-Guard-Token", sharedToken)
            if (body != null) {
                doOutput = true
                setRequestProperty("Content-Type", "application/json; charset=utf-8")
            }
        }
        try {
            if (body != null) {
                connection.outputStream.use { output ->
                    output.write(body.toByteArray(StandardCharsets.UTF_8))
                }
            }
            val code = connection.responseCode
            val stream = if (code in 200..299) connection.inputStream else connection.errorStream
            val response = stream?.bufferedReader(StandardCharsets.UTF_8)?.use { it.readText() }.orEmpty()
            if (code !in 200..299) {
                throw IOException("Orin 返回 HTTP $code${response.takeIf { it.isNotBlank() }?.let { ": $it" }.orEmpty()}")
            }
            code to response
        } finally {
            connection.disconnect()
        }
    }

    private fun parseSnapshot(body: String, fallback: EventStage): EventSnapshot {
        if (body.isBlank()) return EventSnapshot(fallback)
        val json = JSONObject(body)
        return EventSnapshot(
            stage = stageFromWire(json.optString("status")) ?: fallback,
            backendStage = json.optString("stage"),
            message = json.optString("message"),
        )
    }
}

object FakeOrinClient : FallGateway {
    private data class FakeEvent(val startedAtMs: Long, var cancelled: Boolean = false)

    private val events = ConcurrentHashMap<String, FakeEvent>()

    override suspend fun health(): Boolean {
        delay(250)
        return true
    }

    override suspend fun submit(request: FallEventRequest): EventSnapshot {
        delay(250)
        events.putIfAbsent(request.eventId, FakeEvent(System.currentTimeMillis()))
        return EventSnapshot(EventStage.ACCEPTED, message = "Orin 已接收事件")
    }

    override suspend fun status(eventId: String): EventSnapshot {
        delay(100)
        val event = events[eventId] ?: return EventSnapshot(EventStage.FAILED)
        if (event.cancelled) return EventSnapshot(EventStage.CANCELLED)
        val stage = when (System.currentTimeMillis() - event.startedAtMs) {
            in 0..<3_000 -> EventStage.ACCEPTED
            in 3_000..<8_000 -> EventStage.SCANNING
            in 8_000..<15_000 -> EventStage.VERIFYING
            else -> EventStage.NOTIFIED
        }
        return EventSnapshot(stage, message = stage.defaultMessage())
    }

    override suspend fun cancel(eventId: String): EventSnapshot {
        delay(150)
        events[eventId]?.cancelled = true
        return EventSnapshot(EventStage.CANCELLED, message = "告警已取消")
    }
}

fun stageFromWire(value: String): EventStage? = when (value.lowercase()) {
    "accepted" -> EventStage.ACCEPTED
    "scanning" -> EventStage.SCANNING
    "verifying" -> EventStage.VERIFYING
    "notified" -> EventStage.NOTIFIED
    "not_fall" -> EventStage.NOT_FALL
    "cancelled" -> EventStage.CANCELLED
    "failed" -> EventStage.FAILED
    else -> null
}

