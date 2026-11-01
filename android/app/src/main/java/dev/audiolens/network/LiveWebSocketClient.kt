package dev.audiolens.network

import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import java.util.concurrent.TimeUnit

class LiveWebSocketClient(
    private val url: String,
    private val token: String,
    private val onText: (String) -> Unit,
    private val onOpen: () -> Unit = {},
    private val onError: (Throwable?) -> Unit = {},
) {
    private val client = OkHttpClient.Builder()
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .pingInterval(20, TimeUnit.SECONDS)
        .build()
    private var webSocket: WebSocket? = null
    @Volatile private var closing = false

    fun connect() {
        closing = false
        var wsUrl = url.replace("https", "wss").replace("http", "ws")
        if (!wsUrl.endsWith("/ws")) wsUrl = wsUrl.trimEnd('/') + "/ws"
        val request = Request.Builder()
            .url(wsUrl)
            .header("Authorization", "Bearer $token")
            .build()
        webSocket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) { onOpen() }
            override fun onMessage(webSocket: WebSocket, text: String) { onText(text) }
            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) { onError(t) }
            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                if (!closing) onError(null)
            }
        })
    }

    fun sendStart(targetLang: String, mode: String, whisperModel: String) {
        webSocket?.send(
            JSONObject().apply {
                put("type", "start")
                put("source_lang", "auto")
                put("target_lang", targetLang)
                put("mode", mode)
                put("translate", true)
                put("partials", mode == "live")
                put("whisper_model", whisperModel)
                put("session_label", "android")
            }.toString()
        )
    }

    fun sendBinary(data: ByteArray) {
        webSocket?.send(okio.Buffer().write(data, 0, data.size).readByteString(data.size.toLong()))
    }

    fun sendStop() {
        webSocket?.send(JSONObject(mapOf("type" to "stop")).toString())
    }

    fun disconnect() {
        closing = true
        webSocket?.close(1000, "done")
    }
}
