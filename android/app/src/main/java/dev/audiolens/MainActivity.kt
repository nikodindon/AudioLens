package dev.audiolens

import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import dev.audiolens.capture.CaptureService
import dev.audiolens.network.LiveWebSocketClient
import org.json.JSONObject
import dev.audiolens.capture.AudioPlaybackCapture
import java.io.File

class MainActivity : ComponentActivity() {
    var liveRequested = false

    private val projectionLauncher =
        registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
            if (result.resultCode == Activity.RESULT_OK && result.data != null) {
                if (liveRequested) {
                    startStreaming(result.resultCode, result.data!!)
                } else {
                    startCaptureService(result.resultCode, result.data!!, filesDir.resolve("debug_capture.wav").absolutePath)
                }
            }
        }

    private val recordAudioLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    private val notificationsLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme {
                Surface(modifier = Modifier.fillMaxSize()) {
                    AppScreen(this)
                }
            }
        }
    }

    fun requestPermissionsLive(context: Context) {
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            recordAudioLauncher.launch(Manifest.permission.RECORD_AUDIO)
            return
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            notificationsLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
            return
        }
        liveRequested = true
        val projectionManager = getSystemService(Context.MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        projectionLauncher.launch(projectionManager.createScreenCaptureIntent())
    }

    fun requestPermissionsCapture(context: Context) {
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            recordAudioLauncher.launch(Manifest.permission.RECORD_AUDIO)
            return
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            notificationsLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
            return
        }
        liveRequested = false
        val projectionManager = getSystemService(Context.MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        projectionLauncher.launch(projectionManager.createScreenCaptureIntent())
    }

    fun startCaptureService(resultCode: Int, data: Intent, path: String) {
        val intent = Intent(this, CaptureService::class.java).apply {
            action = CaptureService.ACTION_START
            putExtra(CaptureService.EXTRA_RESULT_CODE, resultCode)
            putExtra(CaptureService.EXTRA_RESULT_DATA, data)
            putExtra(CaptureService.EXTRA_OUTPUT_PATH, path)
        }
        startForegroundService(intent)
    }

    private var capture: AudioPlaybackCapture? = null
    private var live: LiveWebSocketClient? = null

    fun startStreaming(resultCode: Int, data: Intent) {
        val prefs = getSharedPreferences("audio_lens", Context.MODE_PRIVATE)
        val url = prefs.getString("server_url", "http://127.0.0.1:8765")!!
        val token = prefs.getString("token", "")!!
        val target = prefs.getString("target_lang", "fr")!!
        val mode = prefs.getString("mode", "live")!!
        val model = prefs.getString("whisper_model", "small")!!
        live = LiveWebSocketClient("$url", token, { text ->
            val obj = JSONObject(text)
            // store transcript via shared composable callback not easy; log
            android.util.Log.i("AudioLens", "WS: $text")
            if (obj.optString("type") == "final" || obj.optString("type") == "done") {
                // no persistent state here; message available in log
            }
        }, {
            // optional diagnostics
        }, { _ ->
            // ignored
        })
        live?.connect()
        live?.sendStart(target, mode, model)
        val projectionManager = getSystemService(Context.MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        val mediaProjection = projectionManager.getMediaProjection(resultCode, data) ?: return
        capture = AudioPlaybackCapture(
            context = this,
            mediaProjection = mediaProjection,
            outputFile = File(filesDir, "stream_capture.wav"),
            onChunk = { chunk -> live?.sendBinary(chunk) },
        )
        capture?.start()
    }

    fun stopStreaming() {
        live?.sendStop()
        capture?.stop()
        capture = null
        live?.disconnect()
        live = null
    }
}

@Composable
private fun AppScreen(activity: MainActivity) {
    val context = LocalContext.current
    val prefs = activity.getSharedPreferences("audio_lens", Context.MODE_PRIVATE)
    var serverUrl by remember { mutableStateOf(prefs.getString("server_url", "http://127.0.0.1:8765")!!) }
    var token by remember { mutableStateOf(prefs.getString("token", "")!!) }
    var targetLang by remember { mutableStateOf(prefs.getString("target_lang", "fr")!!) }
    var mode by remember { mutableStateOf(prefs.getString("mode", "live")!!) }
    var status by remember { mutableStateOf("Pret") }

    Column(
        modifier = Modifier.fillMaxSize().padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text("AudioLens", style = MaterialTheme.typography.headlineLarge)
        OutlinedTextField(value = serverUrl, onValueChange = {
            serverUrl = it
            prefs.edit().putString("server_url", it).apply()
        }, label = { Text("URL serveur") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(value = token, onValueChange = {
            token = it
            prefs.edit().putString("token", it).apply()
        }, label = { Text("Token") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(value = targetLang, onValueChange = {
            targetLang = it
            prefs.edit().putString("target_lang", it).apply()
        }, label = { Text("Langue cible") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(value = mode, onValueChange = {
            mode = it
            prefs.edit().putString("mode", it).apply()
        }, label = { Text("Mode (live/clip)") }, modifier = Modifier.fillMaxWidth())

        Text(status)
        Button(onClick = {
            status = "Demarrage capture..."
            activity.requestPermissionsCapture(context)
        }) { Text("Capture debug") }
        Button(onClick = { status = "Arret capture";  }) { Text("Arreter debug") }
        Button(onClick = {
            status = "Streaming demarre..."
            activity.requestPermissionsLive(context)
        }) { Text("Demarrer streaming") }
        Button(onClick = { status = "Streaming arrete"; activity.stopStreaming() }) { Text("Arreter streaming") }
    }
}