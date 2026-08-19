package com.projectlink.fallguard

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class OrinClientTest {
    @Test
    fun wireStatusesMapToMvpStages() {
        assertEquals(EventStage.ACCEPTED, stageFromWire("accepted"))
        assertEquals(EventStage.SCANNING, stageFromWire("SCANNING"))
        assertEquals(EventStage.VERIFYING, stageFromWire("verifying"))
        assertEquals(EventStage.NOT_FALL, stageFromWire("not_fall"))
        assertEquals(EventStage.CANCELLED, stageFromWire("cancelled"))
        assertNull(stageFromWire("unknown"))
    }
}
