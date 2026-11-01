# Journal de décisions (ADR courts)

Format : contexte → décision → conséquences. Les décisions marquées **[hors README]**
sont des écarts volontaires par rapport au README initial (le README a été mis à jour).

---

## ADR-001 — Android minimum : API 30 (Android 11) **[hors README]**

**Contexte** : le README initial visait `minSdk 29`. La capture de lecture audio
(`AudioPlaybackCapture`) existe depuis l'API 29, mais les exigences de sécurité et
de services au premier plan sont plus simples et mieux documentées à partir de
l'API 30 ; l'utilisateur impose Android 11 minimum.

**Décision** : `minSdk = 30`, `targetSdk = 36` (dernière plateforme installée,
compileSdk 36). Différences gérées :

| Fonctionnalité | API 30–32 | API 33 | API 34+ |
|---|---|---|---|
| `POST_NOTIFICATIONS` | non requise | requise (runtime) | requise |
| `FOREGROUND_SERVICE_MEDIA_PROJECTION` | non déclarée | non déclarée | obligatoire au manifeste + `startForeground` avec le type |
| `mediaProjection` foreground service type | supporté | supporté | obligatoire ; doit démarrer **avant** `getMediaProjection()` |
| MediaProjection consent | à chaque session | à chaque session | à chaque session (token non réutilisable) |
| Overlay | `TYPE_APPLICATION_OVERLAY` | idem | idem ; autorisation spéciale plus stricte sur certains OEM |

**Conséquences** : téléphone de test = realme RMX3834, Android 15 (API 35). Les
branches API 30–34 seront couvertes par le code (vérifications `Build.VERSION`) mais
ne pourront pas être testées sur appareil réel ; ce sera signalé dans `docs/device.md`.

---

## ADR-002 — Configuration serveur : YAML + surcharge d'environnement

**Contexte** : le modèle Whisper doit changer sans toucher au code ; le README
initial proposait un `.env` unique.

**Décision** : `server/config.yaml` (versionné sous `config.example.yaml`, le vrai
fichier est ignoré) est la source principale. Les variables `AUDIOLENS_*` avec
`__` pour la hiérarchie surchargent le fichier (`AUDIOLENS_WHISPER__MODEL=medium`).
Validation Pydantic stricte (`extra="forbid"`), messages d'erreur localisés
(clé + raison), token obligatoire (≥ 16 caractères, placeholders refusés).

**Conséquences** : un `config.yaml` invalide empêche le démarrage avec un message
actionnable ; les secrets peuvent rester hors du fichier via l'environnement.

---

## ADR-003 — `whisper_model` côté client = demande, `allowed_models` = politique

**Décision** : le serveur n'accepte un modèle demandé que s'il figure dans
`whisper.allowed_models` ; sinon il utilise `whisper.model` et renvoie
`whisper_model_accepted: false` + `whisper_model_requested` dans `ready`.

**Conséquences** : un client ne peut pas provoquer le téléchargement d'un modèle
arbitraire (coût disque/RAM) ni saturer la VRAM. Le client doit afficher le
modèle effectif, pas celui qu'il a demandé.

---

## ADR-004 — Types de calcul GPU sur Pascal (GTX 1050, CC 6.1)

**Contexte** : CTranslate2 exige compute capability ≥ 7.0 pour `float16` et
`int8_float16` ; les GPU Pascal (6.1) ne supportent que `int8_float32`, `int8`,
`float32`. Vérifié via `ctranslate2.get_supported_compute_types("cuda")`.

**Décision** : `whisper.compute_type: auto` résout vers `int8_float16` si supporté,
sinon `int8_float32` sur GPU (Pascal), `int8` sur CPU. Un type explicitement
demandé mais non supporté est remplacé par le meilleur type supporté et journalisé.
Si CUDA est demandé et indisponible (pilote, cuDNN, OOM) : repli CPU si
`allow_cpu_fallback: true`, sinon `ModelLoadError` explicite.

**Conséquences** : un configuration portable sur d'autres GPU ; le type effectif
est toujours exposé via `/health`, `ready` et les logs.

---

## ADR-005 — Phase 1 = transcription batch, pas encore de VAD/partiels

**Contexte** : le MVP serveur (Phase 1) doit prouver la connexion, l'authentification,
le protocole et l'inférence Whisper. Le VAD, les partiels et la segmentation sont
explicitement en Phase 2.

**Décision** : la session bufferise les trames PCM (plafonnées par
`server.max_audio_seconds`) et transcrit à la réception de `stop`. Les `final`
ont des timestamps relatifs au début de session. Aucun `partial` n'est émis.

**Conséquences** : la latence mesurée en Phase 1 est celle de l'inférence batch
(et non la latence « fin de phrase ») ; c'est suffisant pour choisir le modèle et
valider le GPU. La classe `Session` garde une API (`accept_audio`/`finalize`)
compatible avec l'ajout du VAD.

---

## ADR-006 — Authentification par token : query ou en-tête Bearer

**Décision** : `?token=` (pratique pour OkHttp/WebSocket) ou
`Authorization: Bearer`. Comparaison à temps constant (`hmac.compare_digest`).
Le token n'est jamais journalisé. Écoute par défaut sur `127.0.0.1` ; pour le
téléphone, écouter l'IP Tailscale (pas `0.0.0.0` sur un réseau non fiable).

**Conséquences** : en développement, `adb reverse tcp:8765 tcp:8765` permet
`ws://localhost:8765/ws` sans exposer le serveur. En production, ACL Tailscale
restreinte au téléphone (documenté dans `docs/troubleshooting.md`).

---

## ADR-007 — Rechargement du modèle à chaud : endpoint protégé

**Contexte** : changer de modèle doit être simple et fiable ; un signal est
difficile à tester, un redémarrage est trivial mais coupe les sessions.

**Décision** : `POST /admin/reload` (token) recharge `config.yaml` puis décharge
et recharge le modèle. Le redémarrage du process reste la procédure de secours
documentée. Pas de surveillance de fichier (évite les rechargements accidentels
pendant une écriture partielle).

**Conséquences** : les sessions en cours ne sont pas interrompues mais la
prochaine inférence attend le rechargement (sémaphore) ; VRAM libérée avant
rechargement.

---

## ADR-008 — Traduction : désactivée en Phase 1

**Décision** : `translation.engine: none` par défaut. La configuration
(`nllb`, `device`, `model`) est déjà validée et présente, mais l'implémentation
arrive en Phase 3. Si un moteur est configuré, le serveur démarre avec un
avertissement et `text_tgt` reste `null` (sauf source = cible).

**Conséquences** : le protocole et la config sont stables avant la Phase 3 ;
aucune fausse promesse côté client.

---

## ADR-009 — Dépendances GPU via wheels pip (cuBLAS + cuDNN 9)

**Contexte** : la machine n'a pas de cuDNN système ; CTranslate2 ≥ 4.5 exige
CUDA 12 + cuDNN 9.

**Décision** : extra pip `gpu` = `nvidia-cublas-cu12` + `nvidia-cudnn-cu12==9.*`,
chargés via `LD_LIBRARY_PATH` calculé par un petit helper (voir
`scripts/gpu_env.sh` / README §9). Ce choix évite une installation système et
reste reproductible.

**Conséquences** : ~1 Go de wheels supplémentaires ; le CPU reste utilisable si
ces bibliothèques sont absentes (repli automatique).

---

## ADR-010 — Modèle par défaut (à compléter après benchmark)

**Contexte** : GTX 1050 4 Go, i5-8300H 4c/8t, modèles `tiny` → `medium` en
`int8_float32` GPU et `int8` CPU.

**Décision** : _à remplir avec les mesures de `docs/benchmark.md`_.

---

## ADR-011 — Structure serveur : package `app`, outils hors package

**Décision** : `server/app` (importable), `server/tools` (scripts), `server/tests`.
Le packaging editable (`pip install -e ".[dev,gpu]"`) simplifie les imports et la
CI. `tools/stream_wav.py` n'a pas de dépendance lourde (numpy + websockets).

**Conséquences** : `uvicorn app.main:create_app --factory` ou
`python -m app.main --config config.yaml` depuis `server/`.
