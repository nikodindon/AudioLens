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
import android.view.Gravity
import android.view.MotionEvent
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.widget.TextView
import android.os.IBinder
import androidx.core.app.NotificationCompat
import dev.audiolens.network.LiveWebSocketClient
import dev.audiolens.network.AudioSendResult
import java.util.concurrent.atomic.AtomicBoolean
import dev.audiolens.state.LiveTranscriptHolder
import dev.audiolens.state.CaptureStatus
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
    private var finished = false
    private var started = false
    private val sendFailureReported = AtomicBoolean(false)
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
        LiveTranscriptHolder.capturing = true
        LiveTranscriptHolder.error = null
        LiveTranscriptHolder.status = CaptureStatus.CONNECTING
        try {
            startCaptureResources(intent)
        } catch (e: Exception) {
            android.util.Log.e("AudioLens", "Unable to start capture", e)
            LiveTranscriptHolder.error = "Impossible de démarrer la capture."
            stopCapture()
        }
    }

    private fun startCaptureResources(intent: Intent) {
        val resultCode = intent.getIntExtra(EXTRA_RESULT_CODE, -1)
        val resultData = requireNotNull(intent.getParcelableExtra<Intent>(EXTRA_RESULT_DATA))
        val outputPath = intent.getStringExtra(EXTRA_OUTPUT_PATH)
        val serverUrl = intent.getStringExtra(EXTRA_SERVER_URL)
        val token = intent.getStringExtra(EXTRA_TOKEN)
        val targetLang = intent.getStringExtra(EXTRA_TARGET_LANG) ?: "fr"
        val mode = intent.getStringExtra(EXTRA_MODE) ?: "live"
        val whisperModel = intent.getStringExtra(EXTRA_WHISPER_MODEL) ?: "small"

        if (serverUrl != null && !token.isNullOrEmpty()) {
            android.util.Log.i("AudioLensWS", "creating ws url=" + serverUrl)
            val client = LiveWebSocketClient(serverUrl, token, { msg ->
                try {
                    val j = org.json.JSONObject(msg)
                    uiHandler.post {
                        val type = j.optString("type")
                        if (stopping && type != "final" && type != "error") return@post
                        when (type) {
                            "ready" -> LiveTranscriptHolder.status = CaptureStatus.ACTIVE
                            "partial" -> LiveTranscriptHolder.status = CaptureStatus.TRANSCRIBING
                            "final" -> {
                                val src = j.optString("text_src", "").trim()
                                val tgt = if (j.isNull("text_tgt")) null else j.optString("text_tgt").trim()
                                val text = tgt?.takeIf { it.isNotBlank() } ?: src
                                if (text.isNotBlank()) {
                                    overlayView?.text = text.take(220)
                                    LiveTranscriptHolder.add(src, tgt)
                                }
                                if (!stopping) LiveTranscriptHolder.status = CaptureStatus.ACTIVE
                            }
                            "error" -> {
                                LiveTranscriptHolder.error = j.optString("message", "Erreur du serveur")
                                LiveTranscriptHolder.status = CaptureStatus.ERROR
                            }
                        }
                    }
                } catch (_: Exception) { }
            }, { android.util.Log.i("AudioLensWS", "WS connected") }, { t ->
                android.util.Log.e("AudioLensWS", "WS failure", t)
                uiHandler.post {
                    if (!stopping) {
                        LiveTranscriptHolder.error = "Connexion au serveur interrompue."
                        LiveTranscriptHolder.status = CaptureStatus.ERROR
                    }
                }
            })
            live = client
            client.connect()
            client.sendStart(targetLang, mode, whisperModel)
        } else {
            LiveTranscriptHolder.error = "Renseignez le serveur et le token dans les réglages."
            LiveTranscriptHolder.status = CaptureStatus.ERROR
        }

        if (Settings.canDrawOverlays(this)) {
            val prefs = getSharedPreferences("audio_lens", MODE_PRIVATE)
            val density = resources.displayMetrics.density
            val params = WindowManager.LayoutParams(
                (resources.displayMetrics.widthPixels * .85f).toInt(),
                WindowManager.LayoutParams.WRAP_CONTENT,
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,
                android.graphics.PixelFormat.TRANSLUCENT,
            ).apply {
                gravity = Gravity.TOP or Gravity.START
                x = prefs.getInt("overlay_x", (resources.displayMetrics.widthPixels * .075f).toInt()).coerceIn(0, (resources.displayMetrics.widthPixels * .15f).toInt())
                y = prefs.getInt("overlay_y", (resources.displayMetrics.heightPixels * .16f).toInt()).coerceIn(0, (resources.displayMetrics.heightPixels * .8f).toInt())
            }
            val windowManager = getSystemService(Context.WINDOW_SERVICE) as WindowManager
            val tv = TextView(this).apply {
                text = "AudioLens · En attente de traduction"
                textSize = prefs.getFloat("overlay_text_size", 18f).coerceIn(14f, 28f)
                background = GradientDrawable().apply {
                    setColor(Color.argb((prefs.getFloat("overlay_opacity", .88f).coerceIn(.55f, 1f) * 255).toInt(), 11, 25, 32))
                    cornerRadius = 18 * density
                    setStroke((1 * density).toInt().coerceAtLeast(1), Color.rgb(72, 128, 124))
                }
                setTextColor(Color.WHITE)
                setPadding((18*density).toInt(), (14*density).toInt(), (18*density).toInt(), (14*density).toInt())
                maxLines = 4
                ellipsize = android.text.TextUtils.TruncateAt.END
                contentDescription = "Traduction AudioLens. Faites glisser pour déplacer."
                var startX = 0
                var startY = 0
                var touchX = 0f
                var touchY = 0f
                setOnTouchListener { view, event ->
                    when (event.action) {
                        MotionEvent.ACTION_DOWN -> {
                            startX = params.x; startY = params.y
                            touchX = event.rawX; touchY = event.rawY
                            true
                        }
                        MotionEvent.ACTION_MOVE -> {
                            params.x = (startX + event.rawX - touchX).toInt().coerceIn(0, (resources.displayMetrics.widthPixels - view.width).coerceAtLeast(0))
                            params.y = (startY + event.rawY - touchY).toInt().coerceIn(0, (resources.displayMetrics.heightPixels - view.height).coerceAtLeast(0))
                            windowManager.updateViewLayout(view, params)
                            true
                        }
                        MotionEvent.ACTION_UP -> {
                            prefs.edit().putInt("overlay_x", params.x).putInt("overlay_y", params.y).apply()
                            view.performClick()
                            true
                        }
                        else -> false
                    }
                }
            }
            windowManager.addView(tv, params)
            overlayView = tv
        }
        val projectionManager = getSystemService(Context.MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        val mediaProjection = requireNotNull(projectionManager.getMediaProjection(resultCode, resultData))
        projection = mediaProjection
        mediaProjection.registerCallback(projectionCallback, uiHandler)
        val outFile = outputPath?.let(::File)
        capture = AudioPlaybackCapture(
            context = this,
            mediaProjection = mediaProjection,
            outputFile = outFile,
            onChunk = { chunk ->
                val result = live?.sendBinary(chunk) ?: AudioSendResult.DISCONNECTED
                if (result != AudioSendResult.SENT && sendFailureReported.compareAndSet(false, true)) {
                    uiHandler.post {
                        if (!stopping) {
                            val reason = if (result == AudioSendResult.OVERLOADED) {
                                "Réseau saturé : capture arrêtée, une partie de l’audio n’a pas été envoyée."
                            } else {
                                "Connexion interrompue : capture arrêtée, une partie de l’audio n’a pas été envoyée."
                            }
                            LiveTranscriptHolder.error = reason
                            overlayView?.text = reason
                            notifyCaptureError(reason)
                            stopCapture()
                        }
                    }
                }
            },
            onFailure = { error ->
                android.util.Log.e("AudioLens", "Capture stopped unexpectedly", error)
                uiHandler.post {
                    LiveTranscriptHolder.error = "La capture audio a été interrompue."
                    stopCapture()
                }
            },
        )
        val started = capture?.start() == true
        if (!started) {
            LiveTranscriptHolder.error = "Impossible de démarrer la capture audio."
            stopCapture()
            return
        }
        android.util.Log.i("AudioLens", "capture started=$started, server=$serverUrl, tokenLen=${token?.length ?: -1}")
        val status = if (started) "Capture en cours" else "Echec de la capture"
        (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager)
            .notify(NOTIFICATION_ID, buildNotification(status))
    }

    private val stopTimeout = Runnable {
        LiveTranscriptHolder.error = "La finalisation a expiré : le dernier segment peut manquer."
        finishStop()
    }

    private fun stopCapture() {
        if (stopping) return
        stopping = true
        LiveTranscriptHolder.status = CaptureStatus.STOPPING
        releaseCapture()
        val client = live
        if (client == null) {
            finishStop()
        } else {
            uiHandler.postDelayed(stopTimeout, 60_000)
            client.finish { success ->
                uiHandler.post {
                    if (finished) return@post
                    if (!success && LiveTranscriptHolder.error == null) {
                        LiveTranscriptHolder.error = "Connexion interrompue avant la fin du dernier segment."
                    }
                    finishStop()
                }
            }
        }
    }

    private fun release(name: String, action: () -> Unit) {
        try { action() } catch (e: Exception) {
            android.util.Log.w("AudioLens", "Unable to release $name", e)
        }
    }

    private fun releaseCapture() {
        release("capture") { capture?.stop() }
        capture = null
        release("projection callback") { projection?.unregisterCallback(projectionCallback) }
        release("projection") { projection?.stop() }
        projection = null
    }

    private fun finishStop() {
        if (finished) return
        finished = true
        uiHandler.removeCallbacks(stopTimeout)
        release("WebSocket") { live?.disconnect() }
        live = null
        overlayView?.let { view ->
            release("overlay") { (getSystemService(Context.WINDOW_SERVICE) as WindowManager).removeView(view) }
        }
        overlayView = null
        LiveTranscriptHolder.capturing = false
        LiveTranscriptHolder.status = if (LiveTranscriptHolder.error == null) CaptureStatus.READY else CaptureStatus.ERROR
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        stopping = true
        finished = true
        uiHandler.removeCallbacks(stopTimeout)
        releaseCapture()
        release("WebSocket") { live?.disconnect() }
        live = null
        overlayView?.let { view ->
            release("overlay") { (getSystemService(Context.WINDOW_SERVICE) as WindowManager).removeView(view) }
        }
        overlayView = null
        LiveTranscriptHolder.capturing = false
        LiveTranscriptHolder.status = if (LiveTranscriptHolder.error == null) CaptureStatus.READY else CaptureStatus.ERROR
        super.onDestroy()
    }

    private fun notifyCaptureError(content: String) {
        val notification = NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("AudioLens · Capture interrompue")
            .setContentText(content)
            .setStyle(NotificationCompat.BigTextStyle().bigText(content))
            .setSmallIcon(android.R.drawable.ic_dialog_alert)
            .setAutoCancel(true)
            .build()
        (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager).notify(2, notification)
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
