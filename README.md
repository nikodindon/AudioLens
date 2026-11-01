# AudioLens 🎧➡️💬

> Transcription (et traduction) en quasi temps réel de **l'audio en cours de lecture sur un smartphone Android**, affichée dans une **bulle flottante** par-dessus n'importe quelle application. Le traitement lourd (Whisper) tourne sur **ton propre serveur**, accessible via **Tailscale**.

*(« AudioLens » est un nom de travail, modifiable.)*

---

## Sommaire

1. [Vision et cas d'usage](#1-vision-et-cas-dusage)
2. [Périmètre](#2-périmètre)
3. [Architecture](#3-architecture)
4. [Stack technique](#4-stack-technique)
5. [Protocole client ↔ serveur](#5-protocole-client--serveur)
6. [Pipeline serveur détaillé](#6-pipeline-serveur-détaillé)
7. [Application Android détaillée](#7-application-android-détaillée)
8. [Structure du dépôt](#8-structure-du-dépôt)
9. [Installation et démarrage](#9-installation-et-démarrage)
10. [Configuration](#10-configuration)
11. [Limites connues](#11-limites-connues)
12. [Sécurité et vie privée](#12-sécurité-et-vie-privée)
13. [Objectifs de performance](#13-objectifs-de-performance)
14. [Roadmap](#14-roadmap)
15. [Stratégie de test](#15-stratégie-de-test)
16. [Risques et mitigations](#16-risques-et-mitigations)
17. [Conventions de développement](#17-conventions-de-développement)
18. [Journal de décisions](#18-journal-de-décisions)
19. [Licence](#19-licence)

---

## 1. Vision et cas d'usage

Quand on regarde une vidéo ou qu'on écoute un vocal (réseau social, messagerie instantanée, lecteur vidéo…) dans une langue que l'on ne comprend pas, on veut **comprendre immédiatement** sans quitter l'app.

**Scénario cible**

1. Une bulle flottante est affichée au-dessus de toutes les apps.
2. L'utilisateur la touche : la capture audio du téléphone démarre.
3. L'audio (PCM 16 kHz mono) est envoyé en flux vers un serveur personnel (via Tailscale).
4. Le serveur détecte la langue, transcrit avec Whisper, traduit vers la langue choisie.
5. Le texte revient sur le téléphone et s'affiche dans un petit panneau translucide, quasi en direct.
6. Un nouveau toucher sur la bulle arrête la capture.

**Principes directeurs**

- **Privé par conception** : aucun service cloud tiers obligatoire ; tout transite dans le tailnet.
- **Léger et discret** : overlay minimaliste, ne gêne pas l'app sous-jacente.
- **Latence maîtrisée** : mieux vaut un texte correct à ~2 s qu'un texte approximatif instantané (mais affichage de partiels possible).
- **Modulaire** : modèle Whisper, moteur de traduction et langue cible interchangeables.

---

## 2. Périmètre

### Inclus (v1)

- Capture de l'audio média interne Android (AudioPlaybackCapture).
- Streaming WebSocket vers le serveur.
- Détection automatique de la langue source.
- Transcription (langue source) + traduction vers la langue cible configurée.
- Overlay : bulle on/off + panneau de sous-titres déplaçable et redimensionnable.
- Écran de configuration (serveur, langue cible, apparence, etc.).

### Hors périmètre (v1)

- Capture des appels téléphoniques (non autorisée par Android).
- Contournement des protections DRM / `allowAudioPlaybackCapture=false`.
- Version iOS.
- Publication sur le Play Store (distribution par APK personnel).
- Diarisation (identification des locuteurs) : envisagée en v2.

---

## 3. Architecture

```
┌──────────────────────────── Smartphone Android ─────────────────────────────┐
│                                                                              │
│  App source (YouTube, WhatsApp, TikTok…)                                     │
│        │ audio                                                               │
│        ▼                                                                     │
│  AudioPlaybackCapture (MediaProjection)                                      │
│        │ PCM 16 kHz mono s16le                                               │
│        ▼                                                                     │
│  CaptureService (foreground, type mediaProjection)                           │
│        │                              ▲                                      │
│        │ frames binaires              │ messages JSON (partial/final)        │
│        ▼                              │                                      │
│  WebSocketClient (OkHttp) ────────────┘                                      │
│                                       │                                      │
│                                       ▼                                      │
│                       OverlayService (bulle + panneau de texte)              │
└──────────────────────────────────┬───────────────────────────────────────────┘
                                   │  WebSocket via Tailscale (WireGuard)
                                   ▼
┌──────────────────────────── Serveur (tailnet) ───────────────────────────────┐
│  FastAPI /ws                                                                 │
│     │                                                                        │
│     ▼                                                                        │
│  Session : ring buffer audio → Silero VAD → segmentation                     │
│     │                                                                        │
│     ▼                                                                        │
│  faster-whisper (détection langue + transcription, partiels + finals)        │
│     │                                                                        │
│     ▼                                                                        │
│  Traduction (NLLB via CTranslate2 | Ollama | Argos | DeepL optionnel)        │
│     │                                                                        │
│     ▼                                                                        │
│  Messages JSON renvoyés au client                                            │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Stack technique

### Serveur

| Brique | Choix | Remarque |
|---|---|---|
| Langage | Python 3.11+ | |
| API | FastAPI + Uvicorn | WebSocket natif |
| STT | `faster-whisper` | CTranslate2 ; GPU CUDA si dispo, sinon CPU int8 |
| VAD | Silero VAD | Évite les hallucinations sur silence/musique |
| Traduction | NLLB-200 distilled (CTranslate2) par défaut | Alternatives : Ollama, Argos Translate, DeepL |
| Config | `server/config.yaml` + Pydantic | Source principale ; surcharge par variables `AUDIOLENS_*` |
| Conteneur | Docker + docker-compose | Image CUDA optionnelle |
| Tests | pytest, pytest-asyncio | |

### Client Android

| Brique | Choix |
|---|---|
| Langage | Kotlin |
| minSdk / targetSdk | 30 (Android 11) / 36 |
| UI paramètres | Jetpack Compose |
| Overlay | `WindowManager` + `TYPE_APPLICATION_OVERLAY` (ComposeView avec LifecycleOwner/SavedStateRegistryOwner manuels) |
| Réseau | OkHttp WebSocket |
| Persistance | DataStore (Preferences) |
| Async | Coroutines + Flow |
| Build | Gradle (Kotlin DSL), version catalog |
| Tests | JUnit, MockK, Turbine |

---

## 5. Protocole client ↔ serveur

Endpoint : `ws://<host-tailscale>:8765/ws?token=<TOKEN>` (ou en-tête
`Authorization: Bearer <TOKEN>`). Le trafic est déjà chiffré par WireGuard dans le
tailnet. Protocole **v1** ; le contrat complet est dans [`docs/protocol.md`](docs/protocol.md).

### Client → serveur

1. **Premier message (texte JSON) : `start`**

```json
{
  "type": "start",
  "protocol": 1,
  "audio": { "format": "pcm_s16le", "sample_rate": 16000, "channels": 1 },
  "source_lang": "auto",
  "target_lang": "fr",
  "whisper_model": "small",
  "translate": true,
  "partials": true
}
```

`whisper_model` est une **demande**, pas un ordre : le serveur ne l'accepte que
s'il figure dans `whisper.allowed_models` (config serveur). Sinon il utilise
`whisper.model` et le signale dans `ready` (`whisper_model_accepted: false`,
`whisper_model_requested`). Le client doit afficher le modèle effectif renvoyé,
jamais celui qu'il a demandé.

2. **Messages binaires** : trames PCM brutes (s16le, 16 kHz, mono), 20 à 100 ms chacune (640 à 3200 octets).
3. **Messages texte optionnels** : `{"type":"config", ...}` (changement de langue à chaud), `{"type":"stop"}`, `{"type":"ping"}`.

### Serveur → client

```json
{
  "type": "ready",
  "session_id": "abc123",
  "server_version": "0.1.0",
  "protocol": 1,
  "whisper_model": "small",
  "whisper_model_requested": "medium",
  "whisper_model_accepted": false,
  "whisper_device": "cuda",
  "whisper_compute_type": "int8_float32",
  "translation_engine": "none",
  "translate": true
}

{ "type": "partial", "segment_id": 12, "text": "こんにちは、今日は", "lang": "ja" }

{
  "type": "final",
  "segment_id": 12,
  "start_ms": 41200,
  "end_ms": 44350,
  "src_lang": "ja",
  "src_lang_prob": 0.97,
  "text_src": "こんにちは、今日は天気がいいですね。",
  "text_tgt": "Bonjour, il fait beau aujourd'hui."
}

{ "type": "error", "code": "model_unavailable", "message": "..." }
{ "type": "pong" }
```

Règles :

- `segment_id` croissant ; un `final` remplace tout `partial` du même `segment_id`.
- Si `src_lang == target_lang`, `text_tgt` = `text_src` (pas de traduction).
- `text_tgt` vaut `null` si `translate` est faux ou si aucun moteur n'est configuré
  (`translation.engine: none`, défaut en Phase 1).
- Le serveur ferme proprement la session sur `stop` ou déconnexion.
- Fermetures : `4401` token invalide, `4429` trop de sessions, `4400` requête
  invalide, `1011` erreur d'inférence. Détail dans `docs/protocol.md`.
- Versionner le protocole (`protocol`) pour pouvoir le faire évoluer.

**Phase 1** : le serveur bufferise l'audio et transcrit à la réception de `stop`
(pas encore de `partial` ni de VAD — Phase 2).

---

## 6. Pipeline serveur détaillé

> **État (Phase 1)** : le pipeline ci-dessous est la cible streaming. En Phase 1,
> le serveur accumule les trames d'une session et transcrit l'audio complet à la
> réception de `stop` (voir ADR-005 dans `docs/decisions.md`). Le VAD, les
> partiels et la segmentation arrivent en Phase 2 ; `Session` expose déjà l'API
> adaptée.

1. **Réception** : les trames PCM sont ajoutées à un ring buffer par session.
2. **VAD** : Silero VAD (fenêtres de 32 ms) détecte début/fin de parole ; fin de segment après N ms de silence (défaut 400 ms), durée max de segment 12 s.
3. **Transcription**
   - *Partiels* : toutes les ~1 s, transcription du segment en cours (buffer croissant) ; stabilisation par accord local (inspiration : `whisper_streaming`) pour éviter le scintillement.
   - *Final* : à la fin du segment, transcription complète avec `beam_size` plus élevé.
   - Options : `condition_on_previous_text=False`, `vad_filter` actif, seuils `no_speech_threshold` / `compression_ratio_threshold` pour filtrer les hallucinations.
   - Langue : détection sur le premier segment puis verrouillage temporaire (re-détection si la confiance chute ou après un long silence).
4. **Traduction** : sur les `final` uniquement (les partiels peuvent être traduits en option si la latence le permet). Si la cible est l'anglais, option d'utiliser directement `task="translate"` de Whisper.
5. **Émission** des messages JSON.
6. **Gestion de charge** : une file d'inférence partagée, 1 à 2 sessions simultanées suffisent ; backpressure si le serveur est saturé (abandon des partiels en priorité).

---

## 7. Application Android détaillée

### Composants

| Composant | Rôle |
|---|---|
| `MainActivity` (Compose) | Onboarding, permissions, paramètres, test de connexion |
| `CaptureService` | Service au premier plan (`foregroundServiceType="mediaProjection"`), gère MediaProjection + `AudioRecord` |
| `AudioCapturer` | `AudioPlaybackCaptureConfiguration` (usages MEDIA, GAME, UNKNOWN), rééchantillonnage vers 16 kHz mono si nécessaire |
| `TranscriptionClient` | WebSocket OkHttp, reconnexion avec backoff, file d'envoi bornée |
| `OverlayService` / `OverlayController` | Bulle flottante (on/off, déplaçable) + panneau de sous-titres |
| `SettingsRepository` | DataStore : URL serveur, token, langue cible, modèle, apparence |

### Permissions et manifeste

- `INTERNET`, `ACCESS_NETWORK_STATE`
- `RECORD_AUDIO` (requise par AudioPlaybackCapture)
- `FOREGROUND_SERVICE` et `FOREGROUND_SERVICE_MEDIA_PROJECTION` (cette dernière
  n'existe qu'à partir d'Android 14 ; déclarée au manifeste, ignorée avant)
- `POST_NOTIFICATIONS` (demande à l'exécution sur Android 13+ uniquement)
- `SYSTEM_ALERT_WINDOW` (overlay ; autorisation spéciale via les paramètres)

### Différences Android 11 → 15 (minSdk 30)

| Sujet | Android 11–12 (API 30–32) | Android 13 (33) | Android 14–15 (34+) |
|---|---|---|---|
| Type FGS `mediaProjection` | supporté | supporté | **obligatoire** au manifeste et dans `startForeground` |
| `FOREGROUND_SERVICE_MEDIA_PROJECTION` | inconnue (ignorée) | inconnue (ignorée) | requise |
| `POST_NOTIFICATIONS` | non requise | runtime | runtime |
| Ordre MediaProjection | recommandé | recommandé | **service FGS démarré avant** `getMediaProjection()` |
| Consentement projection | à chaque session | à chaque session | à chaque session (jeton non réutilisable) |
| Overlay | `TYPE_APPLICATION_OVERLAY` | idem | idem + restrictions OEM |

Le téléphone de test est en **Android 15 / API 35** : les branches 30–33 sont
codées avec `Build.VERSION.SDK_INT` mais ne sont pas vérifiables sur cet appareil
(voir `docs/device.md`).

### Points techniques critiques

- **MediaProjection** : l'autorisation doit être redemandée à chaque session de capture (Android 14+). Le service doit être démarré en premier plan **avant** d'obtenir la projection.
- **Cleartext** : la connexion `ws://` vers une IP/nom Tailscale nécessite une `network_security_config` autorisant le cleartext pour le domaine/IP du tailnet (le trafic reste chiffré par WireGuard). Prévoir aussi `wss://` si l'utilisateur utilise `tailscale serve`.
- **Overlay Compose** : une `ComposeView` hors Activity exige un `LifecycleOwner`, un `ViewModelStoreOwner` et un `SavedStateRegistryOwner` fournis manuellement.
- **Batterie** : arrêt automatique après X minutes sans parole détectée (configurable) ; notification persistante avec action « Arrêter ».
- **Silence imposé** : si l'app source bloque la capture, l'audio reçu est du silence. Détecter ce cas (RMS ≈ 0 pendant N secondes alors que la lecture est active) et informer l'utilisateur dans le panneau.

### UX de l'overlay

- Bulle : 3 états visuels (inactif, connexion, actif) ; appui court = on/off, glisser = déplacer, appui long = menu rapide (taille du texte, langue, fermer).
- Panneau : fond semi-transparent, 2 à 4 lignes, défilement automatique, texte partiel en grisé puis finalisé, bouton pour afficher aussi le texte source, taille et opacité réglables, mode « cliquable à travers » optionnel (`FLAG_NOT_TOUCHABLE`).

---

## 8. Structure du dépôt

```
audiolens/
├── README.md
├── docs/
│   ├── protocol.md
│   ├── decisions.md            # journal de décisions (ADR courts)
│   └── troubleshooting.md
├── server/
│   ├── pyproject.toml
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── .env.example
│   ├── app/
│   │   ├── main.py             # FastAPI + route /ws
│   │   ├── config.py
│   │   ├── session.py          # gestion d'une session de streaming
│   │   ├── vad.py              # Silero VAD
│   │   ├── stt.py              # faster-whisper
│   │   ├── translate/
│   │   │   ├── base.py
│   │   │   ├── nllb.py
│   │   │   ├── ollama.py
│   │   │   └── argos.py
│   │   └── protocol.py         # modèles Pydantic des messages
│   ├── tools/
│   │   └── stream_wav.py       # client de test : envoie un WAV en streaming
│   └── tests/
└── android/
    ├── settings.gradle.kts
    ├── gradle/libs.versions.toml
    └── app/
        └── src/main/
            ├── AndroidManifest.xml
            ├── res/xml/network_security_config.xml
            └── java/…/audiolens/
                ├── ui/          # Compose : settings, onboarding
                ├── capture/     # CaptureService, AudioCapturer
                ├── net/         # TranscriptionClient, modèles de messages
                ├── overlay/     # OverlayService, OverlayController, composables
                └── data/        # SettingsRepository
```

---

## 9. Installation et démarrage

### Prérequis

- Serveur : Linux (ou WSL2) avec **Python 3.11+**, Docker (optionnel), **GPU
  NVIDIA avec CUDA 12 et cuDNN 9** fortement recommandé (ctranlate2 moderne
  exige CUDA ≥ 12,3 + cuDNN 9) ; sinon CPU avec modèle `small` ou `base` en
  `int8`. Carte Pascal (GTX 10xx) : pas de `float16` côté GPU, `int8_float32`.
- Tailscale installé et connecté sur le serveur **et** le téléphone.
- Android Studio (dernière version stable), **JDK 17**, un téléphone **Android 11+**
  avec le mode développeur.

### Serveur

```bash
cd server
cp config.example.yaml config.yaml       # ajuster modèle, token, langue par défaut
# remplacer le token : openssl rand -hex 16
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,gpu]"
source scripts/gpu_env.sh                # CUDA/cuDNN wheels -> LD_LIBRARY_PATH
python -m app.main --config config.yaml
# service : http://127.0.0.1:8765/health
```

**Développement avec adb reverse** (pas besoin de Tailscale) :

```bash
adb reverse tcp:8765 tcp:8765
# puis côté téléphone : ws://localhost:8765/ws?token=...
```

Ou avec Docker : *roadmap Phase 4*. Test sans téléphone :

```bash
TOKEN=$(python -c "import yaml;print(yaml.safe_load(open('config.yaml'))['server']['token'])")
python tools/stream_wav.py samples/ja_sample.wav \
    --target fr --url ws://localhost:8765/ws --token "$TOKEN"
```

Plusieurs modèles disponibles pour la démo :

```bash
python tools/make_samples.py --out samples   # gTTS + ffmpeg (réseau), autres langues avec --langs
```

### Application Android

1. Ouvrir `android/` dans Android Studio.
2. Lancer sur l'appareil (`Run`) ou générer un APK : `./gradlew assembleRelease`.
3. À la première ouverture : accorder micro/notifications, puis « Afficher par-dessus les autres applications ».
4. Dans Paramètres : saisir l'adresse Tailscale du serveur (`100.x.y.z` ou nom MagicDNS), le token, la langue cible. Utiliser « Tester la connexion ».
5. Activer la bulle, ouvrir une vidéo, toucher la bulle, accepter la demande de capture.

---

## 10. Configuration

### Serveur (`config.yaml` + surcharge d'environnement)

Fichier principal : `server/config.yaml` (voir `server/config.example.yaml`, version réelle
ignorée par git). Les variables d'environnement `AUDIOLENS_<SECTION>__<CLÉ>` surchargent
le fichier (ex. `AUDIOLENS_WHISPER__MODEL=medium`). Détail : `docs/decisions.md`
(ADR-002).

Tableau des clés principales (voir `config.example.yaml` pour les défauts) :

| Clé | Valeurs | Notes |
|---|---|---|
| `server.host` | IP | `127.0.0.1` dev/adb reverse, IP Tailscale pour le téléphone ; jamais `0.0.0.0` sans ACL |
| `server.port` | `8765` | |
| `server.token` | ≥ 16 car. | obligatoire, `openssl rand -hex 16` |
| `server.max_sessions` | `2` | |
| `server.max_audio_seconds` | `300` | plafond de buffer par session |
| `whisper.model` | nom ou chemin | modèle par défaut |
| `whisper.device` | `auto`/`cuda`/`cpu` | `auto` = CUDA si dispo |
| `whisper.compute_type` | `auto`/`int8`/`int8_float32`/`int8_float16`/`float32`/… | `auto` = `int8_float32` sur Pascal, `int8_float16` sinon |
| `whisper.beam_size` | `5` | beam final |
| `whisper.cpu_threads` | `4` | ≤ cœurs physiques |
| `whisper.allowed_models` | liste | modèles demandables par le client |
| `whisper.max_loaded_models` | `1` | protection VRAM |
| `whisper.allow_cpu_fallback` | `true` | repli si GPU indisponible / OOM |
| `vad.*` | – | paramètres Phase 2 (déjà validés) |
| `translation.engine` | `none`/`nllb`/`ollama`/`argos` | Phase 3 ; `none` par défaut |
| `translation.device` | `auto`/`cuda`/`cpu` | rester sur CPU pour laisser la VRAM à Whisper |
| `translation.target_lang` | `fr` | langue cible |

**Changer de modèle** (sans toucher au code) :

```bash
# éditer config.yaml : whisper.model (+ allowed_models si besoin), puis :
curl -X POST "http://127.0.0.1:8765/admin/reload?token=$TOKEN"
# ou simplement redémarrer le processus.
```

Le modèle, le device et le compute type réellement utilisés sont journalisés
au démarrage et exposés via `/health` et `ready`.

Passer au modèle `medium` (meilleure précision) :

```yaml
whisper:
  model: medium
  device: auto
  compute_type: auto
  allowed_models: [tiny, base, small, medium]
```

Recommandations pour une **GTX 1050 4 Go (Pascal, int8_float32)** — chiffres dans
`docs/benchmark.md` :

| Modèle | approx. RTF (GPU) | VRAM | Usage |
|---|---|---|---|
| tiny | ~0,05 | ~100 Mo | tests / démos |
| base | ~0,04 | ~130 Mo | faible latence |
| small | ~0,08 | ~400 Mo | **défaut recommandé** |
| medium | ~0,25 | ~1,6 Go | précision maximale plausible |
| large-v3 | – | trop juste | **non recommandé** |

Défaut recommandé : **`small`** sur GPU Pascal, **`base`** sur CPU seul.
Si la cible est l'anglais, en Phase 3 utiliser `task="translate"` direct de
Whisper (plus rapide qu'un NLLB séparé).

### Application (écran Paramètres)

- URL du serveur, token, bouton de test.
- Langue cible (liste), langue source (auto par défaut).
- Modèle Whisper demandé, activation des partiels.
- Affichage : taille du texte, opacité, nombre de lignes, texte source visible ou non, position mémorisée.
- Économie d'énergie : arrêt automatique après inactivité.

---

## 11. Limites connues

- Les applications qui déclarent `allowAudioPlaybackCapture="false"`, ou diffusant du contenu protégé (DRM), ne sont pas capturables (silence reçu).
- Les appels téléphoniques et certains flux VoIP ne sont pas capturables.
- La permission MediaProjection est redemandée à chaque session sur Android récent.
- Whisper peut halluciner sur musique/bruit ; atténué par le VAD mais jamais nul.
- Les langues peu dotées donnent des transcriptions et traductions de moindre qualité.
- Le texte est tributaire de la latence réseau et de la puissance du serveur.

---

## 12. Sécurité et vie privée

- Tout le trafic passe par le tailnet (chiffrement WireGuard) ; ne **jamais** exposer le serveur sur Internet public.
- Token obligatoire côté serveur (en plus de l'ACL Tailscale, à restreindre au téléphone).
- Aucune écriture audio sur disque par défaut ; option de debug explicite et désactivée par défaut.
- Logs sans contenu transcrit par défaut.
- Rappel : transcrire les propos de tiers peut être soumis à des règles légales selon le contexte et le pays ; usage personnel de compréhension recommandé.

---

## 13. Objectifs de performance

| Métrique | Cible |
|---|---|
| Latence « fin de phrase → texte final affiché » | ≤ 3 s (GPU), ≤ 6 s (CPU, modèle small) |
| Latence du premier partiel | ≤ 1,5 s (GPU) |
| WER | Acceptable pour la compréhension (pas de cible stricte) |
| Consommation batterie | < 8 %/h écran allumé en capture continue (à mesurer) |
| Stabilité | Session de 60 min sans crash ni fuite mémoire |
| Reconnexion | Reprise automatique < 5 s après coupure réseau |

---

## 14. Roadmap

Légende : ⬜ à faire · 🟨 en cours · ✅ terminé. Chaque phase se termine par une **démo vérifiable** (critères d'acceptation).

### Phase 0 — Fondations

- ✅ Initialiser le dépôt, structure `server/` et `android/`, `.gitignore`, licence.
- ✅ CI minimale (lint + tests serveur, build Android).
- ✅ Rédiger `docs/protocol.md` à partir de la section 5 (avec logique `allowed_models`).

**Acceptation** : le dépôt se clone, les builds et tests vides passent en CI.

### Phase 1 — Serveur « batch » (MVP serveur)

- ✅ FastAPI + route `/ws` avec authentification par token (query ou Bearer).
- ✅ Modèles Pydantic des messages (`protocol.py`), close codes.
- ✅ Intégration `faster-whisper` : transcription d'un segment + détection de langue.
- ✅ Outil `tools/stream_wav.py` : envoie un WAV en temps réel simulé.
- ✅ Mesure de latence + choix du modèle par défaut (voir `docs/benchmark.md`).
- ✅ Système de config par fichier avec choix de modèle, validation, repli CPU (Phase 0 + changements hors README).

**Acceptation** : un WAV de 60 s en japonais/anglais/espagnol renvoie des `final` corrects via le script de test.

### Phase 2 — Serveur streaming

- ⬜ Ring buffer + Silero VAD + segmentation.
- ⬜ Partiels avec stabilisation (accord local).
- ⬜ Filtres anti-hallucination (seuils, VAD, `condition_on_previous_text=False`).
- ⬜ Verrouillage/re-détection de la langue.
- ⬜ Gestion de plusieurs sessions et de la charge.

**Acceptation** : sur un WAV de 5 min avec silences et musique, pas d'hallucination notable, latence dans les cibles.

### Phase 3 — Traduction

- ⬜ Interface `Translator` + implémentation NLLB (CTranslate2).
- ⬜ Implémentations Ollama et Argos (optionnelles).
- ⬜ Contournement : `task="translate"` si cible = anglais.
- ⬜ Mapping des codes de langue Whisper ↔ NLLB.
- ⬜ Court-circuit si langue source = langue cible.

**Acceptation** : audio japonais → texte français cohérent, latence totale dans les cibles.

### Phase 4 — Dockerisation et exploitation serveur

- ⬜ Dockerfile (CPU) + variante CUDA, `docker-compose.yml`.
- ⬜ Healthcheck, logs structurés, `.env.example` complet.
- ⬜ Guide de déploiement derrière Tailscale (ACL, MagicDNS, `tailscale serve` en option).

**Acceptation** : `docker compose up` sur une machine propre suffit à démarrer le service.

### Phase 5 — Android : capture seule

- ⬜ Projet Kotlin, thème, onboarding des permissions.
- ⬜ `CaptureService` au premier plan + MediaProjection + `AudioRecord` playback capture.
- ⬜ Enregistrement local en WAV (mode debug) pour valider la capture sur YouTube, WhatsApp, Instagram, TikTok, Telegram.
- ⬜ Détection du « silence imposé » (app non capturable).
- ⬜ Tableau de compatibilité des apps dans `docs/`.

**Acceptation** : un WAV audible est produit depuis au moins 3 apps différentes sur le téléphone cible.

### Phase 6 — Android : streaming vers le serveur

- ⬜ `TranscriptionClient` OkHttp : connexion, `start`, envoi des trames, réception des messages.
- ⬜ `network_security_config` pour le tailnet.
- ⬜ Reconnexion avec backoff, file d'envoi bornée, gestion du réseau perdu.
- ⬜ Écran de paramètres (DataStore) et test de connexion.
- ⬜ Affichage provisoire du texte dans l'Activity.

**Acceptation** : lecture d'une vidéo étrangère → texte traduit visible dans l'app, de bout en bout.

### Phase 7 — Overlay

- ⬜ Permission « par-dessus les autres apps » et `OverlayService`.
- ⬜ Bulle flottante déplaçable (états : inactif / connexion / actif).
- ⬜ Panneau de sous-titres : partiels grisés, finaux, défilement, texte source optionnel.
- ⬜ Réglages d'apparence (taille, opacité, lignes), mémorisation de la position.
- ⬜ Intégration : la bulle contrôle `CaptureService`.

**Acceptation** : depuis YouTube/WhatsApp, un toucher sur la bulle lance la capture et les sous-titres apparaissent par-dessus l'app.

### Phase 8 — Robustesse et finitions

- ⬜ Arrêt automatique sur inactivité, notification avec action « Arrêter ».
- ⬜ Gestion des cas limites : rotation, mode multi-fenêtre, écran verrouillé, perte de focus audio.
- ⬜ Mesure batterie et optimisation (taille des trames, compression optionnelle Opus).
- ⬜ Messages d'erreur clairs dans le panneau.
- ⬜ Documentation utilisateur et `troubleshooting.md`.

**Acceptation** : session de 60 min stable ; checklist de tests manuels validée.

### Phase 9 — Version 1.0

- ⬜ Revue de sécurité (token, logs, exposition réseau).
- ⬜ APK signé, notes de version, tag `v1.0.0`.

### Backlog (v2 et au-delà)

- ⬜ Historique et export des transcriptions (texte, SRT).
- ⬜ Diarisation (locuteurs).
- ⬜ Glossaire personnalisé et prompt initial Whisper.
- ⬜ Mode hors-ligne partiel (petit modèle sur le téléphone).
- ⬜ Compression Opus pour limiter la bande passante.
- ⬜ Tile de réglages rapides Android pour démarrer/arrêter.
- ⬜ Vérification d'une éventuelle option `wss://` + certificat Tailscale.

---

## 15. Stratégie de test

**Serveur**

- Unitaires : VAD (fixtures audio), segmentation, mapping de langues, traducteurs (mocks).
- Intégration : session WebSocket complète avec WAV de référence (`tools/stream_wav.py`), assertions sur la séquence `ready → partial* → final`.
- Performance : script de benchmark (latence par modèle/device), résultats consignés dans `docs/decisions.md`.
- Corpus de test court : 3 à 5 langues, avec silence, musique, parole superposée.

**Android**

- Unitaires : parsing des messages, file d'envoi, repository de paramètres.
- Instrumentés : cycle de vie des services, affichage de l'overlay.
- Manuels (checklist) : capture par app, coupure réseau, changement de langue, écran verrouillé, Android 10 / 13 / 14+.

---

## 16. Risques et mitigations

| Risque | Impact | Mitigation |
|---|---|---|
| App source non capturable | Fonction inutilisable sur cette app | Détection du silence + message ; tableau de compatibilité |
| Latence trop élevée (CPU) | Expérience dégradée | Modèle plus petit, `distil-whisper`, partiels, GPU |
| Hallucinations Whisper | Texte faux | VAD, seuils, `condition_on_previous_text=False` |
| Restrictions Android 14/15 sur MediaProjection / services | Capture interrompue | Suivre la doc officielle, tests sur versions récentes |
| Overlay bloqué par certains constructeurs | Bulle invisible | Guide d'autorisations par marque dans `troubleshooting.md` |
| Consommation batterie | Usage limité | Arrêt auto, trames adaptées, option Opus |
| Qualité de traduction faible sur certaines langues | Incompréhension | Choix de moteur configurable, affichage du texte source |

---

## 17. Conventions de développement

- **Git** : branches `feature/…`, commits conventionnels (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`), une PR (ou merge) par étape de roadmap.
- **Python** : `ruff` + `black`, typage avec `mypy` (mode raisonnable), `async` de bout en bout, aucune opération bloquante dans la boucle d'événements (inférence dans un executor/thread).
- **Kotlin** : `ktlint`/`detekt`, architecture simple (services + repositories + Flow), pas de sur-ingénierie.
- **Docs** : toute décision structurante ajoutée à `docs/decisions.md` (contexte, décision, conséquences).
- **Secrets** : jamais commités ; `.env` ignoré, `.env.example` fourni.
- **Définition de « terminé »** pour une tâche : code, tests, doc mise à jour, case cochée dans cette roadmap.

---

## 18. Journal de décisions

*(À compléter au fil du projet dans `docs/decisions.md`. Décisions initiales :)*

| # | Décision | Raison |
|---|---|---|
| 1 | Client en Kotlin natif | Accès direct à MediaProjection, services et overlay |
| 2 | WebSocket + PCM brut | Simplicité, faible latence ; Opus en option plus tard |
| 3 | `faster-whisper` | Meilleur rapport vitesse/mémoire que Whisper original |
| 4 | Traduction en étage séparé | Whisper ne traduit que vers l'anglais |
| 5 | Distribution par APK personnel | Évite les contraintes du Play Store (overlay, MediaProjection) |

---

## 19. Licence

À définir (MIT suggérée pour un projet personnel).
