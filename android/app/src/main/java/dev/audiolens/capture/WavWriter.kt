package dev.audiolens.capture

import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream

class WavWriter(private val file: File, private val sampleRate: Int, private val channels: Int) {
    private val pcmFile = File(file.parentFile ?: file, "${file.name}.pcm")
    private val lock = Any()
    private val out = FileOutputStream(pcmFile, false)
    private var bytesWritten = 0

    fun write(shorts: ShortArray, offset: Int, length: Int) {
        synchronized(lock) {
            val buffer = ByteArray(length * 2)
            var i = 0
            for (j in 0 until length) {
                val v = shorts[offset + j]
                buffer[i++] = (v.toInt() and 0xff).toByte()
                buffer[i++] = ((v.toInt() shr 8) and 0xff).toByte()
            }
            out.write(buffer)
            bytesWritten += buffer.size
        }
    }

    fun close() {
        synchronized(lock) {
            out.flush()
            out.close()
            writeComplete()
        }
    }

    private fun writeComplete() {
        file.parentFile?.mkdirs()
        FileOutputStream(file, false).use { wav ->
            wav.write("RIFF".toByteArray())
            writeLE32(wav, 36 + bytesWritten)
            wav.write("WAVE".toByteArray())
            wav.write("fmt ".toByteArray())
            writeLE32(wav, 16)
            writeLE16(wav, 1)
            writeLE16(wav, channels)
            writeLE32(wav, sampleRate)
            writeLE32(wav, sampleRate * channels * 2)
            writeLE16(wav, channels * 2)
            writeLE16(wav, 16)
            wav.write("data".toByteArray())
            writeLE32(wav, bytesWritten)
            FileInputStream(pcmFile).use { it.copyTo(wav) }
        }
        pcmFile.delete()
    }

    private fun writeLE32(out: FileOutputStream, v: Int) {
        out.write(v and 0xff)
        out.write((v ushr 8) and 0xff)
        out.write((v ushr 16) and 0xff)
        out.write((v ushr 24) and 0xff)
    }

    private fun writeLE16(out: FileOutputStream, v: Int) {
        out.write(v and 0xff)
        out.write((v ushr 8) and 0xff)
    }
}
