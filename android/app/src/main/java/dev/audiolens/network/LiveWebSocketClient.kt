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
    private val stopHandshake = StopHandshake()

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
            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                stopHandshake.complete(false)
                if (!closing) onError(t)
            }
            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                webSocket.close(code, null)
            }
            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                if (closing) stopHandshake.complete(code == 1000) else onError(null)
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

    fun sendBinary(data: ByteArray): AudioSendResult {
        val socket = webSocket ?: return AudioSendResult.DISCONNECTED
        if (closing) return AudioSendResult.DISCONNECTED
        if (!AudioQueuePolicy.canEnqueue(socket.queueSize(), data.size)) return AudioSendResult.OVERLOADED
        return if (socket.send(okio.Buffer().write(data).readByteString())) AudioSendResult.SENT else AudioSendResult.DISCONNECTED
    }

    fun finish(onComplete: (Boolean) -> Unit) {
        if (!stopHandshake.begin(onComplete)) return
        closing = true
        if (webSocket?.send("{\"type\":\"stop\"}") != true) stopHandshake.complete(false)
    }

    fun disconnect() {
        closing = true
        webSocket?.cancel()
        stopHandshake.complete(false)
    }
}
