package dev.audiolens

import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.net.Uri
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import dev.audiolens.capture.CaptureService
import dev.audiolens.state.CaptureStatus
import dev.audiolens.state.LiveTranscriptHolder
import dev.audiolens.ui.AudioLensApp

class MainActivity : ComponentActivity() {
    private val projectionLauncher =
        registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
            if (result.resultCode == Activity.RESULT_OK && result.data != null) {
                startCaptureService(result.resultCode, result.data!!)
            }
        }
    private val recordAudioLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (granted) requestCapture() else showPermissionError("L’autorisation audio est nécessaire à la capture.")
        }
    private val notificationsLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { requestCapture() }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { AudioLensApp(this) }
    }

    private fun showPermissionError(message: String) {
        LiveTranscriptHolder.error = message
        LiveTranscriptHolder.status = CaptureStatus.ERROR
    }

    fun requestCapture() {
        if (LiveTranscriptHolder.capturing) return
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            recordAudioLauncher.launch(Manifest.permission.RECORD_AUDIO)
            return
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED &&
            !notificationRequested
        ) {
            notificationRequested = true
            notificationsLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
            return
        }
        LiveTranscriptHolder.error = null
        LiveTranscriptHolder.status = CaptureStatus.READY
        val manager = getSystemService(Context.MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        projectionLauncher.launch(manager.createScreenCaptureIntent())
    }

    private var notificationRequested = false

    fun openOverlaySettings() {
        startActivity(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName")))
    }

    private fun startCaptureService(resultCode: Int, data: Intent) {
        val prefs = getSharedPreferences("audio_lens", MODE_PRIVATE)
        val intent = Intent(this, CaptureService::class.java).apply {
            action = CaptureService.ACTION_START
            putExtra(CaptureService.EXTRA_RESULT_CODE, resultCode)
            putExtra(CaptureService.EXTRA_RESULT_DATA, data)
            putExtra(CaptureService.EXTRA_OUTPUT_PATH, filesDir.resolve("debug_capture.wav").absolutePath)
            putExtra(CaptureService.EXTRA_SERVER_URL, prefs.getString("server_url", "http://127.0.0.1:8765"))
            putExtra(CaptureService.EXTRA_TOKEN, prefs.getString("token", ""))
            putExtra(CaptureService.EXTRA_TARGET_LANG, prefs.getString("target_lang", "fr"))
            putExtra(CaptureService.EXTRA_MODE, prefs.getString("mode", "live"))
            putExtra(CaptureService.EXTRA_WHISPER_MODEL, prefs.getString("whisper_model", "small"))
        }
        startForegroundService(intent)
    }

    fun stopCapture() { stopService(Intent(this, CaptureService::class.java)) }
}
