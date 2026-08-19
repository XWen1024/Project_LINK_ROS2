package com.projectlink.fallguard

import android.content.Context
import java.io.IOException
import java.net.ConnectException
import java.net.HttpURLConnection
import java.net.SocketTimeoutException
import java.net.URL
import java.net.UnknownHostException
import java.nio.charset.StandardCharsets
import java.util.concurrent.ConcurrentHashMap
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import org.json.JSONObject

interface FallGateway {
    suspend fun health(): GatewayHealthResult
    suspend fun submit(request: FallEventRequest): EventStage
    suspend fun status(eventId: String): EventStage
    suspend fun cancel(eventId: String): EventStage
}

object FallGatewayFactory {
    fun create(settings: AppSettings, context: Context? = null): FallGateway =
        if (settings.simulationEnabled) {
            FakeOrinClient
        } else {
            RealOrinClient(
                baseUrl = settings.orinBaseUrl,
                sharedToken = settings.sharedToken,
                networkDescription = context?.let(NetworkDiagnostics::describe) ?: "未知",
            )
        }
}

class RealOrinClient(
    baseUrl: String,
    private val sharedToken: String,
    private val networkDescription: String = "未知",
) : FallGateway {
    private val baseUrl = baseUrl.trim().trimEnd('/')

    override suspend fun health(): GatewayHealthResult {
        if (sharedToken.isBlank()) {
            return failureResult(
                summary = "共享 Token 未填写",
                hint = "请把 Orin 的 FALL_GUARD_TOKEN 填入 App 设置；弹窗不会显示 Token 内容。",
            )
        }
        return try {
            val response = request(method = "GET", path = "/health")
            val payload = JSONObject(response.body)
            val healthy = payload.optString("status") == "ok"
            val readiness = listOf(
                "camera_ready" to payload.optBoolean("camera_ready"),
                "model_ready" to payload.optBoolean("model_ready"),
                "vision_ready" to payload.optBoolean("vision_ready"),
                "notification_ready" to payload.optBoolean("notification_ready"),
                "coordinator_ready" to payload.optBoolean("coordinator_ready"),
            ).joinToString(separator = "\n") { (name, ready) -> "$name: $ready" }
            GatewayHealthResult(
                success = healthy,
                summary = if (healthy) "Orin Gateway 健康检查通过" else "Orin 返回了非健康状态",
                details = diagnosticHeader(response.url, "HTTP ${response.code}") +
                    "\n$readiness\n\n响应：\n${payload.toString(2)}",
            )
        } catch (error: Exception) {
            healthFailure(error)
        }
    }

    override suspend fun submit(request: FallEventRequest): EventStage {
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
        return parseStage(request("POST", "/api/fall", body).body, EventStage.ACCEPTED)
    }

    override suspend fun status(eventId: String): EventStage =
        parseStage(request("GET", "/api/fall/$eventId").body, EventStage.ACCEPTED)

    override suspend fun cancel(eventId: String): EventStage =
        parseStage(request("POST", "/api/fall/$eventId/cancel", "{}").body, EventStage.CANCELLED)

    private suspend fun request(method: String, path: String, body: String? = null): GatewayHttpResponse =
        withContext(Dispatchers.IO) {
            val target = validateGatewayBaseUrl(baseUrl)
            val requestUrl = URL(target.toString().trimEnd('/') + path)
            val connection = (requestUrl.openConnection() as HttpURLConnection).apply {
                requestMethod = method
                connectTimeout = 4_000
                readTimeout = 6_000
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
                val responseBody = stream?.bufferedReader(StandardCharsets.UTF_8)?.use { it.readText() }.orEmpty()
                if (code !in 200..299) {
                    throw GatewayHttpException(code, requestUrl, responseBody)
                }
                GatewayHttpResponse(code, requestUrl, responseBody)
            } finally {
                connection.disconnect()
            }
        }

    private fun healthFailure(error: Exception): GatewayHealthResult {
        val summary: String
        val hint: String
        val status: String
        when (error) {
            is GatewayHttpException -> {
                status = "HTTP ${error.statusCode}"
                when (error.statusCode) {
                    401 -> {
                        summary = "Token 被 Orin 拒绝"
                        hint = "确认 App 中的共享 Token 与 Orin FALL_GUARD_TOKEN 完全一致。"
                    }
                    404 -> {
                        summary = "Orin 上没有 /health 接口"
                        hint = "确认填写的是 Gateway 根地址，例如 http://10.255.176.119:8765。"
                    }
                    else -> {
                        summary = "Orin 返回 HTTP ${error.statusCode}"
                        hint = "查看响应和 Orin Gateway 日志。"
                    }
                }
                return GatewayHealthResult(
                    false,
                    summary,
                    diagnosticHeader(error.url, status) +
                        "\n提示：$hint\n\n响应：\n${error.responseBody.take(MAX_DIAGNOSTIC_BODY)}",
                )
            }
            is IllegalArgumentException -> {
                summary = error.message ?: "Orin 地址无效"
                hint = "手机应填写 http://10.255.176.119:8765；127.0.0.1 指向手机自己。"
                status = "请求未发送"
            }
            is UnknownHostException -> {
                summary = "无法解析 Orin 主机名"
                hint = "优先填写 Orin 局域网 IP，例如 http://10.255.176.119:8765。"
                status = error.javaClass.simpleName
            }
            is ConnectException -> {
                summary = "连接被拒绝"
                hint = "确认 Gateway 正在运行并监听 0.0.0.0:8765，且手机与 Orin 在同一局域网。"
                status = error.javaClass.simpleName
            }
            is SocketTimeoutException -> {
                summary = "连接 Orin 超时"
                hint = "检查手机是否仍在使用蜂窝数据；请连接与 Orin 相同的 Wi-Fi/热点。"
                status = error.javaClass.simpleName
            }
            is IOException -> {
                summary = "网络请求失败"
                hint = "检查手机 Wi-Fi、Orin IP、端口和局域网隔离设置。"
                status = error.javaClass.simpleName
            }
            else -> {
                summary = "健康检查失败"
                hint = "查看错误类型并核对 Orin 地址。"
                status = error.javaClass.simpleName
            }
        }
        return failureResult(summary, hint, status, error.message)
    }

    private fun failureResult(
        summary: String,
        hint: String,
        status: String = "配置错误",
        errorMessage: String? = null,
    ): GatewayHealthResult {
        val target = "$baseUrl/health"
        val errorLine = errorMessage?.takeIf { it.isNotBlank() }?.let { "\n错误：$it" }.orEmpty()
        return GatewayHealthResult(
            success = false,
            summary = summary,
            details = diagnosticHeader(target, status) + "\n提示：$hint$errorLine",
        )
    }

    private fun diagnosticHeader(target: Any, result: String): String =
        "目标：$target\n当前网络：$networkDescription\nToken：${if (sharedToken.isBlank()) "未填写" else "已填写"}\n结果：$result"

    private fun parseStage(body: String, fallback: EventStage): EventStage {
        if (body.isBlank()) return fallback
        return stageFromWire(JSONObject(body).optString("status")) ?: fallback
    }

    private companion object {
        const val MAX_DIAGNOSTIC_BODY = 1_000
    }
}

internal fun validateGatewayBaseUrl(raw: String): URL {
    require(raw.isNotBlank()) { "Orin 地址不能为空" }
    val url = runCatching { URL(raw.trim().trimEnd('/')) }
        .getOrElse { throw IllegalArgumentException("Orin 地址格式错误，必须包含 http:// 或 https://") }
    require(url.protocol == "http" || url.protocol == "https") { "Orin 地址只支持 http:// 或 https://" }
    require(url.host.isNotBlank()) { "Orin 地址缺少主机或 IP" }
    require(url.path.isBlank() || url.path == "/") { "Orin 地址只填写根地址，不要附加 /health" }
    val host = url.host.lowercase()
    require(host != "localhost" && host != "0.0.0.0" && host != "::1" && !host.startsWith("127.")) {
        "手机不能使用 $host；它指向手机自身或监听地址"
    }
    return url
}

private data class GatewayHttpResponse(val code: Int, val url: URL, val body: String)

private class GatewayHttpException(
    val statusCode: Int,
    val url: URL,
    val responseBody: String,
) : IOException(
    "HTTP $statusCode${responseBody.takeIf { it.isNotBlank() }?.let { ": ${it.take(500)}" }.orEmpty()}",
)

object FakeOrinClient : FallGateway {
    private data class FakeEvent(val startedAtMs: Long, var cancelled: Boolean = false)
    private val events = ConcurrentHashMap<String, FakeEvent>()

    override suspend fun health(): GatewayHealthResult {
        delay(250)
        return GatewayHealthResult(
            success = true,
            summary = "本地模拟后端可用",
            details = "模式：本地模拟\n未向 Orin 发送网络请求。",
        )
    }

    override suspend fun submit(request: FallEventRequest): EventStage {
        delay(250)
        events.putIfAbsent(request.eventId, FakeEvent(System.currentTimeMillis()))
        return EventStage.ACCEPTED
    }

    override suspend fun status(eventId: String): EventStage {
        delay(100)
        val event = events[eventId] ?: return EventStage.FAILED
        if (event.cancelled) return EventStage.CANCELLED
        return when (System.currentTimeMillis() - event.startedAtMs) {
            in 0..<3_000 -> EventStage.ACCEPTED
            in 3_000..<8_000 -> EventStage.SCANNING
            in 8_000..<15_000 -> EventStage.VERIFYING
            else -> EventStage.NOTIFIED
        }
    }

    override suspend fun cancel(eventId: String): EventStage {
        delay(150)
        events[eventId]?.cancelled = true
        return EventStage.CANCELLED
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
