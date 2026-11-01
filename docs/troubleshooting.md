# Dépannage

## Serveur

### `CUDA backend unavailable` / repli CPU inattendu

1. Vérifier le pilote : `nvidia-smi`.
2. Vérifier que les wheels CUDA/cuDNN sont chargées :

```bash
cd server
source scripts/gpu_env.sh
python -c "import ctranslate2; print(ctranslate2.get_cuda_device_count()); print(sorted(ctranslate2.get_supported_compute_types('cuda')))"
```

Attendu sur la GTX 1050 : `1` et `['float32', 'int8', 'int8_float32']`.
Si la liste est vide, `nvidia-cublas-cu12` / `nvidia-cudnn-cu12` ne sont pas
installés : `pip install -e ".[gpu]"`.

3. `float16` / `int8_float16` échouent sur Pascal : c'est normal, CTranslate2 ne
   les supporte pas sur compute capability 6.1. Laisser `compute_type: auto`
   (résout `int8_float32`) ou choisir explicitement `int8_float32`.

### `model_unavailable` ou téléchargement bloqué

- Vérifier l'espace disque (`medium` ≈ 1,5 Go, `small` ≈ 500 Mo).
- Tester le téléchargement : le serveur journalise le modèle au démarrage.
- Pré-télécharger : `python -c "from faster_whisper import WhisperModel; WhisperModel('small')"`.
- Derrière un proxy : `HTTPS_PROXY`, `HF_HOME` pour changer le cache.

### `configuration file not found`

```bash
cd server
cp config.example.yaml config.yaml
# puis remplacer le token par : openssl rand -hex 16
```

### `token is a placeholder` / `too short`

Le token doit faire ≥ 16 caractères et ne pas être une valeur d'exemple.
Générer : `openssl rand -hex 16`.

### Le téléphone n'atteint pas le serveur

- Développement : `adb reverse tcp:8765 tcp:8765`, puis URL
  `ws://localhost:8765/ws?token=...`.
- Tailscale : `tailscale status`, `tailscale ip -4` ; configurer
  `server.host` avec l'IP Tailscale (ou `0.0.0.0` seulement si l'ACL restreint).
- Vérifier l'ACL Tailscale (le téléphone doit pouvoir joindre le port 8765).
- Android bloque le cleartext par défaut : voir `docs/device.md` et la
  `network_security_config`. En Phase 6, utiliser `wss://` via `tailscale serve`
  est la solution recommandée hors développement.

### Latence trop élevée

Voir `docs/benchmark.md` et `README.md` §10. Sur GTX 1050 : `small` en
`int8_float32` est le compromis par défaut ; `base` pour plus de vitesse ;
`medium` si la précision prime. Vérifier que le serveur utilise bien `cuda` via
`/health` (`device`, `compute_type`).

### Theora / audio non transcrit (silence)

Whisper n'est pas un détecteur de silence : la Phase 2 ajoute le VAD. En Phase 1,
un WAV silencieux peut produire des hallucinations ; ce sera filtré ensuite.

## Android (à compléter aux Phases 5–8)

- Autorisation « Afficher par-dessus les autres applications » absente chez
  certains constructeurs (realme/Xiaomi/OPPO) : chercher dans les paramètres
  « Affichage par-dessus les autres applications » / « Overlay ».
- MediaProjection refusé : l'autorisation est demandée à chaque session sur
  Android 14+. Le service doit être au premier plan **avant** l'obtention de la
  projection.
- Notifications invisibles : accorder `POST_NOTIFICATIONS` (Android 13+).
- Capture silencieuse : l'app source a `allowAudioPlaybackCapture=false` ou le
  contenu est protégé (DRM). Tester avec une autre app.
