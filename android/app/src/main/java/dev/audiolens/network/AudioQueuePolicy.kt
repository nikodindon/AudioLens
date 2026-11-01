package dev.audiolens.network

/** 8 seconds of 16 kHz mono PCM16. No frames are discarded silently. */
internal object AudioQueuePolicy {
    const val MAX_BYTES = 256_000L

    fun canEnqueue(queuedBytes: Long, frameBytes: Int): Boolean =
        frameBytes >= 0 && queuedBytes >= 0 && queuedBytes <= MAX_BYTES - frameBytes
}

enum class AudioSendResult { SENT, OVERLOADED, DISCONNECTED }
