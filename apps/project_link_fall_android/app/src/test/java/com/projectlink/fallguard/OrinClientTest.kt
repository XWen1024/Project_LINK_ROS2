package com.projectlink.fallguard

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Test

class OrinClientTest {
    @Test
    fun wireStatusesMapToMvpStages() {
        assertEquals(EventStage.ACCEPTED, stageFromWire("accepted"))
        assertEquals(EventStage.SCANNING, stageFromWire("SCANNING"))
        assertEquals(EventStage.NOT_FALL, stageFromWire("not_fall"))
        assertEquals(EventStage.CANCELLED, stageFromWire("cancelled"))
        assertNull(stageFromWire("unknown"))
    }

    @Test
    fun phoneRejectsLoopbackAndListenerAddresses() {
        assertThrows(IllegalArgumentException::class.java) {
            validateGatewayBaseUrl("http://127.0.0.1:8765")
        }
        assertThrows(IllegalArgumentException::class.java) {
            validateGatewayBaseUrl("http://0.0.0.0:8765")
        }
        assertThrows(IllegalArgumentException::class.java) {
            validateGatewayBaseUrl("http://localhost:8765")
        }
    }

    @Test
    fun phoneAcceptsOrinLanRootAddress() {
        val url = validateGatewayBaseUrl("http://10.255.176.119:8765")
        assertEquals("10.255.176.119", url.host)
        assertEquals(8765, url.port)
    }
}
