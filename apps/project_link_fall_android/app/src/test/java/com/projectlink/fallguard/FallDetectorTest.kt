package com.projectlink.fallguard

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test

class FallDetectorTest {
    @Test
    fun freeFallImpactAndStillnessTriggersDetection() {
        val detector = FallDetector(ImuThresholds(stillnessSeconds = 2f))

        assertNull(detector.onFrame(frame(0, accelerationG = 0.4f)))
        assertNull(detector.onFrame(frame(100, accelerationG = 3.1f)))

        var detection: FallDetection? = null
        for (timestamp in 600L..2_100L step 100L) {
            detection = detector.onFrame(frame(timestamp, accelerationG = 1.01f, gyro = 2f)) ?: detection
        }

        assertNotNull(detection)
        assertEquals(3.1f, detection!!.summary.peakAccelG, 0.01f)
    }

    @Test
    fun ordinaryImpactWithoutFreeFallOrRotationIsRejected() {
        val detector = FallDetector(ImuThresholds(stillnessSeconds = 1f))

        assertNull(detector.onFrame(frame(0, accelerationG = 2.7f)))
        var detection: FallDetection? = null
        for (timestamp in 500L..1_200L step 100L) {
            detection = detector.onFrame(frame(timestamp, accelerationG = 1f, gyro = 1f)) ?: detection
        }

        assertNull(detection)
    }

    @Test
    fun orientationChangeCanReplaceFreeFallSignal() {
        val detector = FallDetector(ImuThresholds(stillnessSeconds = 1f))

        detector.onFrame(frame(0, accelerationG = 2.8f, pitch = 0f))
        var detection: FallDetection? = null
        for (timestamp in 500L..1_200L step 100L) {
            detection = detector.onFrame(
                frame(timestamp, accelerationG = 1f, gyro = 1f, pitch = 70f),
            ) ?: detection
        }

        assertNotNull(detection)
    }

    private fun frame(
        timestampMs: Long,
        accelerationG: Float,
        gyro: Float = 0f,
        pitch: Float? = 0f,
    ) = SensorFrame(
        timestampMs = timestampMs,
        accelerationG = accelerationG,
        angularVelocityDegPerSecond = gyro,
        pitchDeg = pitch,
        rollDeg = 0f,
    )
}
