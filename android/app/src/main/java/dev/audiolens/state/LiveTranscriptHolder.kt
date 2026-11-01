package dev.audiolens.state

import androidx.compose.runtime.mutableStateListOf

object LiveTranscriptHolder {
    val lines = mutableStateListOf<String>()
}
