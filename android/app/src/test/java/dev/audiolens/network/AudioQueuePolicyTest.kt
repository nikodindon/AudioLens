package dev.audiolens.network

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AudioQueuePolicyTest {
    @Test fun capsQueuedAudioAtEightSeconds() {
        assertTrue(AudioQueuePolicy.canEnqueue(0, 3200))
        assertTrue(AudioQueuePolicy.canEnqueue(252800, 3200))
        assertFalse(AudioQueuePolicy.canEnqueue(252801, 3200))
        assertFalse(AudioQueuePolicy.canEnqueue(0, 256001))
        assertFalse(AudioQueuePolicy.canEnqueue(-1, 3200))
    }
}
