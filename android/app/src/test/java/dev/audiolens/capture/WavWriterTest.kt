package dev.audiolens.capture

import java.nio.file.Files
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

class WavWriterTest {
    @Test
    fun closeFinalizesWavAndRemovesTemporaryPcm() {
        val dir = Files.createTempDirectory("audiolens-wav-test").toFile()
        try {
            val file = dir.resolve("capture.wav")
            val writer = WavWriter(file, 16000, 1)
            writer.write(shortArrayOf(0x1234, -2), 0, 2)
            writer.close()

            val bytes = file.readBytes()
            assertEquals(48, bytes.size)
            assertEquals("RIFF", String(bytes, 0, 4, Charsets.US_ASCII))
            assertEquals("WAVE", String(bytes, 8, 4, Charsets.US_ASCII))
            assertEquals(4, bytes[40].toInt())
            assertArrayEquals(byteArrayOf(0x34, 0x12, 0xfe.toByte(), 0xff.toByte()), bytes.copyOfRange(44, 48))
            assertFalse(dir.resolve("capture.wav.pcm").exists())
        } finally {
            dir.deleteRecursively()
        }
    }
}
