package dev.audiolens.state

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue

enum class CaptureStatus { READY, CONNECTING, ACTIVE, TRANSCRIBING, STOPPING, ERROR }

data class TranscriptLine(val source: String, val translation: String?) {
    val display: String get() = translation?.takeIf { it.isNotBlank() } ?: source
}

// Updated on the main thread by the service; history lasts for the app process only.
object LiveTranscriptHolder {
    val lines = mutableStateListOf<TranscriptLine>()
    var status by mutableStateOf(CaptureStatus.READY)
    var capturing by mutableStateOf(false)
    var error by mutableStateOf<String?>(null)

    fun add(source: String, translation: String?) {
        if (source.isBlank() && translation.isNullOrBlank()) return
        lines.add(TranscriptLine(source, translation))
        if (lines.size > 100) lines.removeAt(0)
    }
}
