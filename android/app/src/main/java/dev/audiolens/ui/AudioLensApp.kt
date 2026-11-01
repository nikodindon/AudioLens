package dev.audiolens.ui

import android.provider.Settings
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import dev.audiolens.MainActivity
import dev.audiolens.state.CaptureStatus
import dev.audiolens.state.LiveTranscriptHolder

private val Aqua = Color(0xFF77D9CC)
private val dark = darkColorScheme(primary = Aqua, onPrimary = Color(0xFF00201C), background = Color(0xFF0B141C), surface = Color(0xFF111F29), surfaceVariant = Color(0xFF1B3039), onSurface = Color(0xFFE3F0EF))
private val light = lightColorScheme(primary = Color(0xFF006B61), background = Color(0xFFF4F9F7), surface = Color.White, surfaceVariant = Color(0xFFDFEFEB))

@Composable
fun AudioLensApp(activity: MainActivity) {
    val prefs = remember { activity.getSharedPreferences("audio_lens", 0) }
    var page by rememberSaveable { mutableStateOf("home") }
    var overlayAllowed by remember { mutableStateOf(Settings.canDrawOverlays(activity)) }
    val owner = LocalLifecycleOwner.current
    DisposableEffect(owner) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) overlayAllowed = Settings.canDrawOverlays(activity)
        }
        owner.lifecycle.addObserver(observer)
        onDispose { owner.lifecycle.removeObserver(observer) }
    }
    val darkTheme = isSystemInDarkTheme()
    SideEffect {
        activity.window.decorView.windowInsetsController?.setSystemBarsAppearance(
            if (darkTheme) 0 else android.view.WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS or android.view.WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS,
            android.view.WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS or android.view.WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS,
        )
    }
    MaterialTheme(colorScheme = if (darkTheme) dark else light) {
        Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
            Column(Modifier.safeDrawingPadding().fillMaxSize()) {
                Row(Modifier.fillMaxWidth().padding(horizontal = 24.dp, vertical = 16.dp), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text("AUDIO / LENS", color = MaterialTheme.colorScheme.primary, fontWeight = FontWeight.Bold, letterSpacing = 2.sp, style = MaterialTheme.typography.labelMedium)
                        Text(when (page) { "settings" -> "Réglages"; "history" -> "Traductions récentes"; else -> "Comprendre, en direct." }, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.SemiBold)
                    }
                    TextButton(onClick = { page = if (page == "home") "settings" else "home" }) { Text(if (page == "home") "Réglages" else "Accueil") }
                }
                when (page) {
                    "settings" -> SettingsPage(activity, prefs, overlayAllowed)
                    "history" -> HistoryPage()
                    else -> HomePage(activity, overlayAllowed, onHistory = { page = "history" })
                }
            }
        }
    }
}

@Composable
private fun HomePage(activity: MainActivity, overlayAllowed: Boolean, onHistory: () -> Unit) {
    val status = LiveTranscriptHolder.status
    val capturing = LiveTranscriptHolder.capturing
    val title = when (status) {
        CaptureStatus.READY -> "Prêt à traduire"
        CaptureStatus.CONNECTING -> "Connexion en cours"
        CaptureStatus.ACTIVE -> "Capture active"
        CaptureStatus.TRANSCRIBING -> "Transcription en cours"
        CaptureStatus.ERROR -> "Une action est nécessaire"
    }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 24.dp), horizontalAlignment = Alignment.CenterHorizontally) {
        Spacer(Modifier.height(24.dp))
        LensMark(capturing)
        Spacer(Modifier.height(24.dp))
        Text(title, style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.SemiBold, textAlign = TextAlign.Center)
        Spacer(Modifier.height(8.dp))
        Text(LiveTranscriptHolder.error ?: if (capturing) "Écoutez votre contenu : la traduction apparaît au-dessus de votre application." else "Choisissez une application, lancez son contenu et lisez la traduction en direct.", textAlign = TextAlign.Center, color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.bodyMedium)
        Spacer(Modifier.height(24.dp))
        Button(onClick = { if (capturing) activity.stopCapture() else activity.requestCapture() }, modifier = Modifier.fillMaxWidth().heightIn(min = 56.dp), shape = RoundedCornerShape(18.dp)) {
            Text(if (capturing) "Arrêter la capture" else "Démarrer la capture", fontWeight = FontWeight.Bold)
        }
        if (!overlayAllowed) {
            Spacer(Modifier.height(12.dp))
            OutlinedButton(onClick = activity::openOverlaySettings, modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(18.dp)) { Text("Autoriser l’affichage par-dessus les applications") }
        }
        Spacer(Modifier.height(32.dp))
        HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
        Row(Modifier.fillMaxWidth().padding(top = 12.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("TRADUCTIONS RÉCENTES", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.primary, letterSpacing = 1.sp)
                Spacer(Modifier.height(8.dp))
                Text(LiveTranscriptHolder.lines.lastOrNull()?.display ?: "Vos traductions apparaîtront ici.", maxLines = 3, style = MaterialTheme.typography.bodyLarge)
            }
            TextButton(onClick = onHistory) { Text("Voir tout") }
        }
        Spacer(Modifier.height(24.dp))
    }
}

@Composable
private fun LensMark(active: Boolean) {
    val color = MaterialTheme.colorScheme.primary
    Surface(shape = CircleShape, color = MaterialTheme.colorScheme.surfaceVariant, modifier = Modifier.size(150.dp)) {
        Canvas(Modifier.fillMaxSize().padding(18.dp)) {
            drawCircle(color.copy(alpha = .22f), style = Stroke(width = 1.dp.toPx()))
            drawCircle(color.copy(alpha = .42f), radius = size.minDimension * .38f, style = Stroke(width = 1.dp.toPx()))
            val heights = if (active) listOf(.17f, .34f, .55f, .32f, .19f) else listOf(.14f, .24f, .36f, .24f, .14f)
            heights.forEachIndexed { i, h ->
                val x = size.width * (.30f + i * .10f)
                drawLine(color, androidx.compose.ui.geometry.Offset(x, size.height * (.5f - h / 2)), androidx.compose.ui.geometry.Offset(x, size.height * (.5f + h / 2)), strokeWidth = 4.dp.toPx(), cap = StrokeCap.Round)
            }
        }
    }
}

@Composable
private fun HistoryPage() {
    val lines = LiveTranscriptHolder.lines
    if (lines.isEmpty()) {
        Box(Modifier.fillMaxSize().padding(32.dp), contentAlignment = Alignment.Center) { Text("Aucune traduction pour le moment. Les résultats de cette session apparaîtront ici.", textAlign = TextAlign.Center, color = MaterialTheme.colorScheme.onSurfaceVariant) }
    } else {
        LazyColumn(contentPadding = PaddingValues(24.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            items(lines.reversed()) { line ->
                Surface(shape = RoundedCornerShape(20.dp), color = MaterialTheme.colorScheme.surface) {
                    Column(Modifier.fillMaxWidth().padding(20.dp)) {
                        Text(line.display, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.Medium)
                        if (!line.translation.isNullOrBlank() && line.source.isNotBlank() && line.source != line.translation) {
                            Spacer(Modifier.height(8.dp))
                            Text(line.source, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
            }
            item { Text("Historique conservé pendant cette session uniquement.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
        }
    }
}

@Composable
private fun SettingsPage(activity: MainActivity, prefs: android.content.SharedPreferences, overlayAllowed: Boolean) {
    var url by remember { mutableStateOf(prefs.getString("server_url", "http://127.0.0.1:8765") ?: "") }
    var token by remember { mutableStateOf(prefs.getString("token", "") ?: "") }
    var language by remember { mutableStateOf(prefs.getString("target_lang", "fr") ?: "fr") }
    var model by remember { mutableStateOf(prefs.getString("whisper_model", "small") ?: "small") }
    var mode by remember { mutableStateOf(prefs.getString("mode", "live") ?: "live") }
    var size by remember { mutableFloatStateOf(prefs.getFloat("overlay_text_size", 18f)) }
    var opacity by remember { mutableFloatStateOf(prefs.getFloat("overlay_opacity", .88f)) }
    var diagnosticWav by remember { mutableStateOf(prefs.getBoolean("diagnostic_wav", false)) }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 24.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        SectionLabel("CONNEXION")
        OutlinedTextField(url, { url = it; prefs.edit().putString("server_url", it).apply() }, Modifier.fillMaxWidth(), label = { Text("Adresse du serveur") }, singleLine = true)
        OutlinedTextField(token, { token = it; prefs.edit().putString("token", it).apply() }, Modifier.fillMaxWidth(), label = { Text("Token") }, visualTransformation = PasswordVisualTransformation(), singleLine = true)
        SectionLabel("TRADUCTION")
        OutlinedTextField(language, { language = it; prefs.edit().putString("target_lang", it).apply() }, Modifier.fillMaxWidth(), label = { Text("Langue cible (code)") }, singleLine = true)
        OutlinedTextField(model, { model = it; prefs.edit().putString("whisper_model", it).apply() }, Modifier.fillMaxWidth(), label = { Text("Modèle Whisper") }, singleLine = true)
        Text("Mode de capture", style = MaterialTheme.typography.titleSmall)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf("live" to "En direct", "clip" to "Clip").forEach { (value, label) ->
                FilterChip(selected = mode == value, onClick = { mode = value; prefs.edit().putString("mode", value).apply() }, label = { Text(label) })
            }
        }
        if (mode == "clip") Text("Le mode clip dépend de la prise en charge par le serveur ; sa prise en charge actuelle est limitée.", style = MaterialTheme.typography.bodySmall)
        SectionLabel("SUPERPOSITION")
        Text("Taille du texte : ${size.toInt()} sp")
        Slider(size, { size = it; prefs.edit().putFloat("overlay_text_size", it).apply() }, valueRange = 14f..28f)
        Text("Opacité : ${(opacity * 100).toInt()} %")
        Slider(opacity, { opacity = it; prefs.edit().putFloat("overlay_opacity", it).apply() }, valueRange = .55f..1f)
        Text("Les changements de superposition s’appliquent à la prochaine capture. Faites glisser le bandeau pour le déplacer.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        if (!overlayAllowed) OutlinedButton(onClick = activity::openOverlaySettings) { Text("Autoriser la superposition") }
        SectionLabel("DIAGNOSTIC")
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("Enregistrer l’audio en WAV")
                Text("Fichier privé sur cet appareil, remplacé à chaque capture.", style = MaterialTheme.typography.bodySmall)
            }
            Switch(checked = diagnosticWav, onCheckedChange = {
                diagnosticWav = it
                prefs.edit().putBoolean("diagnostic_wav", it).apply()
            })
        }
        Spacer(Modifier.height(24.dp))
    }
}

@Composable
private fun SectionLabel(text: String) {
    Text(text, Modifier.padding(top = 12.dp), color = MaterialTheme.colorScheme.primary, style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.Bold, letterSpacing = 1.5.sp)
}
