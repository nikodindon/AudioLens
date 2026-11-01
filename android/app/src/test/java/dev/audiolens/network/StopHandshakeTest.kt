package dev.audiolens.network

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class StopHandshakeTest {
    @Test fun completionWaitsForCloseAndRunsOnce() {
        val handshake = StopHandshake()
        val results = mutableListOf<Boolean>()
        assertTrue(handshake.begin { results.add(it) })
        assertTrue(results.isEmpty())
        assertFalse(handshake.begin { results.add(it) })
        handshake.complete(true)
        handshake.complete(false)
        assertEquals(listOf(true), results)
    }

    @Test fun timeoutOrFailureIsReported() {
        val handshake = StopHandshake()
        val results = mutableListOf<Boolean>()
        handshake.begin { results.add(it) }
        handshake.complete(false)
        handshake.complete(true)
        assertEquals(listOf(false), results)
    }
}
