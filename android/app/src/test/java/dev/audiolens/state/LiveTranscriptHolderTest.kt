package dev.audiolens.state

import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Test

class LiveTranscriptHolderTest {
    @After fun clear() = LiveTranscriptHolder.lines.clear()

    @Test fun keepsTranslationAndSourceAndBoundsHistory() {
        repeat(101) { LiveTranscriptHolder.add("source $it", "traduction $it") }
        assertEquals(100, LiveTranscriptHolder.lines.size)
        assertEquals("source 1", LiveTranscriptHolder.lines.first().source)
        assertEquals("traduction 100", LiveTranscriptHolder.lines.last().display)
        LiveTranscriptHolder.add("texte source", null)
        assertEquals("texte source", LiveTranscriptHolder.lines.last().display)
    }
}
