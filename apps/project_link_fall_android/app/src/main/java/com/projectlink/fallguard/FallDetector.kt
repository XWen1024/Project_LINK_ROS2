package com.projectlink.fallguard

import kotlin.math.abs
import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.min

data class SensorFrame(
    val timestampMs: Long,
    val accelerationG: Float,
    val angularVelocityDegPerSecond: Float,
    val pitchDeg: Float?,
    val rollDeg: Float?,
)

data class FallDetection(val summary: ImuSummary)

class FallDetector(
    private val thresholds: ImuThresholds,
    private val cooldownMs: Long = 60_000,
) {
    private data class Candidate(
        val impactAtMs: Long,
        val referencePitchDeg: Float?,
        val referenceRollDeg: Float?,
        val freeFallObserved: Boolean,
        var peakAccelG: Float,
        var maxOrientationChangeDeg: Float = 0f,
        var quietAccelTotal: Float = 0f,
        var quietGyroTotal: Float = 0f,
        var quietSamples: Int = 0,
    )

    private var lastFreeFallAtMs: Long? = null
    private var candidate: Candidate? = null
    private var cooldownUntilMs: Long = 0

    fun onFrame(frame: SensorFrame): FallDetection? {
        if (frame.accelerationG <= thresholds.freeFallG) {
            lastFreeFallAtMs = frame.timestampMs
        }

        val active = candidate
        if (active == null) {
            if (frame.timestampMs >= cooldownUntilMs && frame.accelerationG >= thresholds.impactG) {
                val freeFallObserved = lastFreeFallAtMs?.let { frame.timestampMs - it <= 1_000 } == true
                candidate = Candidate(
                    impactAtMs = frame.timestampMs,
                    referencePitchDeg = frame.pitchDeg,
                    referenceRollDeg = frame.rollDeg,
                    freeFallObserved = freeFallObserved,
                    peakAccelG = frame.accelerationG,
                )
            }
            return null
        }

        active.peakAccelG = max(active.peakAccelG, frame.accelerationG)
        active.maxOrientationChangeDeg = max(active.maxOrientationChangeDeg, orientationDelta(active, frame))

        val elapsedMs = frame.timestampMs - active.impactAtMs
        if (elapsedMs >= 500) {
            active.quietAccelTotal += abs(frame.accelerationG - 1f)
            active.quietGyroTotal += frame.angularVelocityDegPerSecond
            active.quietSamples += 1
        }

        val stillnessMs = (thresholds.stillnessSeconds * 1_000).toLong().coerceAtLeast(500)
        if (elapsedMs < stillnessMs) return null

        candidate = null
        val averageAccelDelta = active.quietAccelTotal / active.quietSamples.coerceAtLeast(1)
        val averageGyro = active.quietGyroTotal / active.quietSamples.coerceAtLeast(1)
        val orientationOrFreeFall = active.freeFallObserved ||
            active.maxOrientationChangeDeg >= thresholds.orientationChangeDeg
        val lowMotion = averageAccelDelta <= 0.15f && averageGyro <= 15f
        if (!orientationOrFreeFall || !lowMotion) return null

        cooldownUntilMs = frame.timestampMs + cooldownMs
        return FallDetection(
            ImuSummary(
                peakAccelG = active.peakAccelG,
                orientationChangeDeg = active.maxOrientationChangeDeg,
                inactivityMs = elapsedMs,
            ),
        )
    }

    fun reset() {
        lastFreeFallAtMs = null
        candidate = null
        cooldownUntilMs = 0
    }

    private fun orientationDelta(candidate: Candidate, frame: SensorFrame): Float {
        val referencePitch = candidate.referencePitchDeg ?: return 0f
        val referenceRoll = candidate.referenceRollDeg ?: return 0f
        val pitch = frame.pitchDeg ?: return 0f
        val roll = frame.rollDeg ?: return 0f
        val pitchDelta = shortestAngleDelta(referencePitch, pitch)
        val rollDelta = shortestAngleDelta(referenceRoll, roll)
        return min(180f, hypot(pitchDelta.toDouble(), rollDelta.toDouble()).toFloat())
    }

    private fun shortestAngleDelta(first: Float, second: Float): Float {
        val raw = abs(first - second) % 360f
        return min(raw, 360f - raw)
    }
}
