package dev.audiolens.capture

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioPlaybackCaptureConfiguration
import android.media.AudioRecord
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import java.io.File
import java.util.concurrent.atomic.AtomicBoolean

class AudioPlaybackCapture(
    private val context: Context,
    private val mediaProjection: MediaProjection,
    private val outputFile: File,
    private val sampleRate: Int = 16000,
    private val onRms: (Float) -> Unit = {},
) {
    private val running = AtomicBoolean(false)
    private var record: AudioRecord? = null
    private var thread: Thread? = null

    fun start(): Boolean {
        val config = AudioPlaybackCaptureConfiguration.Builder(mediaProjection)
            .addMatchingUsage(AudioAttributes.USAGE_MEDIA)
            .addMatchingUsage(AudioAttributes.USAGE_GAME)
            .addMatchingUsage(AudioAttributes.USAGE_UNKNOWN)
            .build()
        val format = AudioFormat.Builder()
            .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
            .setSampleRate(sampleRate)
            .setChannelMask(AudioFormat.CHANNEL_IN_MONO)
            .build()
        val minBuffer = AudioRecord.getMinBufferSize(sampleRate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        if (minBuffer <= 0) return false
        val audioRecord = AudioRecord.Builder()
            .setAudioFormat(format)
            .setBufferSizeInBytes(minBuffer * 2)
            .setAudioPlaybackCaptureConfig(config)
            .build()
        if (audioRecord.state != AudioRecord.STATE_INITIALIZED) {
            audioRecord.release()
            return false
        }
        record = audioRecord
        running.set(true)
        audioRecord.startRecording()
        thread = Thread({
            val wav = WavWriter(outputFile, sampleRate, 1)
            val buffer = ShortArray(minBuffer)
            var total = 0L
            var lastRms = 0f
            while (running.get()) {
                val read = audioRecord.read(buffer, 0, buffer.size)
                if (read > 0) {
                    wav.write(buffer, 0, read)
                    total += read
                    var sum = 0.0
                    for (i in 0 until read) {
                        val v = buffer[i].toDouble()
                        sum += v * v
                    }
                    lastRms = kotlin.math.sqrt(sum / read).toFloat()
                    onRms(lastRms)
                }
            }
            audioRecord.stop()
            audioRecord.release()
            record = null
            wav.close()
        }, "AudioLens-Capture").apply { start() }
        return true
    }

    fun stop() {
        running.set(false)
        thread?.join(2000)
    }

    fun isRunning(): Boolean = running.get()
}
