package dev.audiolens.capture

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.view.WindowManager
import android.widget.TextView
import android.os.IBinder
import androidx.core.app.NotificationCompat
import dev.audiolens.network.LiveWebSocketClient
import dev.audiolens.state.LiveTranscriptHolder
import java.io.File

class CaptureService : Service() {
    companion object {
        const val CHANNEL_ID = "audiolens_capture"
        const val NOTIFICATION_ID = 1
        const val ACTION_START = "dev.audiolens.action.START"
        const val ACTION_STOP = "dev.audiolens.action.STOP"
        const val EXTRA_RESULT_CODE = "result_code"
        const val EXTRA_RESULT_DATA = "result_data"
        const val EXTRA_OUTPUT_PATH = "output_path"
        const val EXTRA_SERVER_URL = "server_url"
        const val EXTRA_TOKEN = "token"
        const val EXTRA_TARGET_LANG = "target_lang"
        const val EXTRA_MODE = "mode"
        const val EXTRA_WHISPER_MODEL = "whisper_model"
    }

    private var projection: MediaProjection? = null
    private var capture: AudioPlaybackCapture? = null
    private var live: LiveWebSocketClient? = null
    private val uiHandler = Handler(Looper.getMainLooper())
    private var overlayView: TextView? = null
    private var stopping = false
    private var started = false
    private val projectionCallback = object : MediaProjection.Callback() {
        override fun onStop() {
            stopCapture()
        }
    }

    override fun onCreate() {
        super.onCreate()
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "AudioLens capture",
                NotificationManager.IMPORTANCE_LOW,
            )
            manager.createNotificationChannel(channel)
        }
        startForeground(
            NOTIFICATION_ID,
            buildNotification("Pret"),
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION
            } else 0,
        )
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> startCapture(intent)
            ACTION_STOP -> stopCapture()
        }
        return START_NOT_STICKY
    }

    private fun startCapture(intent: Intent) {
        // A new consent result must not replace resources of an active session.
        if (started || stopping) return
        started = true
        try {
            startCaptureResources(intent)
        } catch (e: Exception) {
            android.util.Log.e("AudioLens", "Unable to start capture", e)
            stopCapture()
        }
    }

    private fun startCaptureResources(intent: Intent) {
        val resultCode = intent.getIntExtra(EXTRA_RESULT_CODE, -1)
        val resultData = requireNotNull(intent.getParcelableExtra<Intent>(EXTRA_RESULT_DATA))
        val outputPath = requireNotNull(intent.getStringExtra(EXTRA_OUTPUT_PATH))
        val serverUrl = intent.getStringExtra(EXTRA_SERVER_URL)
        val token = intent.getStringExtra(EXTRA_TOKEN)
        val targetLang = intent.getStringExtra(EXTRA_TARGET_LANG) ?: "fr"
        val mode = intent.getStringExtra(EXTRA_MODE) ?: "live"
        val whisperModel = intent.getStringExtra(EXTRA_WHISPER_MODEL) ?: "small"

        if (serverUrl != null && !token.isNullOrEmpty()) {
            android.util.Log.i("AudioLensWS", "creating ws url=" + serverUrl)
            val client = LiveWebSocketClient(serverUrl, token, { msg ->
                android.util.Log.i("AudioLensWS", "RX=" + msg)
                try {
                    val j = org.json.JSONObject(msg)
                    // Show a single, readable final segment; ignore noisy partials.
                    if (j.optString("type") != "final") { /* ignore */ } else {
                    val src = j.optString("text_src", "").trim()
                    val tgt = j.optString("text_tgt", "").trim()
                    val text = if (tgt.isNotBlank()) tgt.take(220) else src.take(220)
                    if (text.isNotBlank() && text.length >= 5) uiHandler.post {
                        if (stopping) return@post
                        overlayView?.text = text
                        overlayView?.maxLines = 4
                        overlayView?.ellipsize = android.text.TextUtils.TruncateAt.END
                        LiveTranscriptHolder.lines.add(text)
                    }
                    }
                } catch (_: Exception) {}
            }, { android.util.Log.i("AudioLensWS", "WS connected") }, { t -> android.util.Log.e("AudioLensWS", "WS failure", t) })
            live = client
            client.connect()
            client.sendStart(targetLang, mode, whisperModel)
        } else {
            android.util.Log.w("AudioLensWS", "WS skipped serverUrl=" + serverUrl + " token empty=" + token.isNullOrEmpty())
        }

        if (Settings.canDrawOverlays(this)) {
            val params = WindowManager.LayoutParams(
                WindowManager.LayoutParams.WRAP_CONTENT,
                WindowManager.LayoutParams.WRAP_CONTENT,
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,
                android.graphics.PixelFormat.TRANSLUCENT,
            )
            val tv = TextView(this).apply {
                text = "AudioLens..."
                textSize = 16f
                setBackgroundColor(android.graphics.Color.argb(200, 0, 0, 0))
                setTextColor(android.graphics.Color.WHITE)
                setPadding(20, 14, 20, 14)
                alpha = 0.95f
            }
            (getSystemService(Context.WINDOW_SERVICE) as WindowManager).addView(tv, params)
            overlayView = tv
        }
        val projectionManager = getSystemService(Context.MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        val mediaProjection = requireNotNull(projectionManager.getMediaProjection(resultCode, resultData))
        projection = mediaProjection
        mediaProjection.registerCallback(projectionCallback, uiHandler)
        val outFile = File(outputPath)
        capture = AudioPlaybackCapture(
            context = this,
            mediaProjection = mediaProjection,
            outputFile = outFile,
            onChunk = { chunk -> live?.sendBinary(chunk) },
            onFailure = { error ->
                android.util.Log.e("AudioLens", "Capture stopped unexpectedly", error)
                uiHandler.post { stopCapture() }
            },
        )
        val started = capture?.start() == true
        if (!started) {
            stopCapture()
            return
        }
        android.util.Log.i("AudioLens", "capture started=$started, server=$serverUrl, tokenLen=${token?.length ?: -1}")
        val status = if (started) "Capture en cours (${outFile.name})" else "Echec de la capture"
        (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager)
            .notify(NOTIFICATION_ID, buildNotification(status))
    }

    private fun stopCapture() {
        if (stopping) return
        stopping = true
        cleanupResources()
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    private fun cleanupResources() {
        // Release each resource even if another one has already failed.
        fun release(name: String, action: () -> Unit) {
            try { action() } catch (e: Exception) {
                android.util.Log.w("AudioLens", "Unable to release $name", e)
            }
        }
        release("capture") { capture?.stop() }
        capture = null
        release("WebSocket stop") { live?.sendStop() }
        overlayView?.let {
            release("overlay") {
                (getSystemService(Context.WINDOW_SERVICE) as WindowManager).removeView(it)
            }
        }
        overlayView = null
        release("WebSocket") { live?.disconnect() }
        live = null
        release("projection callback") { projection?.unregisterCallback(projectionCallback) }
        release("projection") { projection?.stop() }
        projection = null
    }

    override fun onDestroy() {
        stopping = true
        cleanupResources()
        super.onDestroy()
    }

    private fun buildNotification(content: String): Notification {
        val stopIntent = Intent(this, CaptureService::class.java).apply { action = ACTION_STOP }
        val stopPending = PendingIntent.getService(
            this,
            0,
            stopIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("AudioLens")
            .setContentText(content)
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .addAction(0, "Arreter", stopPending)
            .setOngoing(true)
            .build()
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
