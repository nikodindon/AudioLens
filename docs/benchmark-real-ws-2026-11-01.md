# Référence de streaming isolé — 1 novembre 2026

## Périmètre

Cinq phrases anglaises fictives ont été synthétisées localement avec FFmpeg / libflite, sans réseau ni conversation privée. Elles ont été envoyées en PCM 16 kHz mono par WebSocket, par trames de 100 ms à cadence audio, à une instance FastAPI indépendante liée à un port local réservé par le script. Aucun appel n'a été envoyé au serveur du téléphone ou au traducteur partagé. Le processus de test a utilisé `nice 19`, un thread CPU et `HF_HUB_OFFLINE=1` avec le modèle `faster-whisper-tiny` déjà en cache. Le serveur de test a été arrêté par son propre processus superviseur en fin d'essai.

Le serveur actif utilisait `small`, CUDA/int8, `beam_size=8` et un traducteur externe ; cet essai utilise **tiny, CPU/int8, beam_size=5, traduction désactivée et partiels désactivés**. Il n'est donc pas une référence absolue des performances du parcours Telegram. Les phrases synthétiques sont en anglais et ne permettent pas d'évaluer la précision russe → français.

Code de base : `07b73cc`, avec des modifications locales préexistantes dans `session.py` et `translate/openai.py` ; elles n’ont pas été commitées. La traduction étant désactivée et la cible `fr`, ces changements locaux ne sont pas censés modifier le chemin STT mesuré.

Versions : faster-whisper 1.2.1, CTranslate2 4.8.4, onnxruntime 1.31.1, websockets 17.4, uvicorn 0.54.0 et FFmpeg 6.1.1. CPU Intel i5-8300H ; VAD seuil 0,5, silence 500 ms, durée maximale de segment 5 s ; source `en`, cible `fr`, `translate=false`. Les métriques locales ont été activées sur le seul serveur de test.

## Résultats observés

| Mesure | Médiane | p95 | Nombre |
|---|---:|---:|---:|
| VAD par trame (ms) | 3,319 | 6,704 | 201 |
| Attente du sémaphore STT (ms) | 0,005 | 0,007 | 10 |
| Inférence STT complète (ms) | 797,248 | 878,140 | 10 |
| Retour de `stop` jusqu'à fermeture (ms) | 841,808 | 1 003,066 | 5 |
| Arrivée du `final` moins timestamp de fin de segment serveur (ms) | 1 290,271 | 1 343,421 | 6 |
| RTF mural de session | 1,217 | 1,251 | 5 |

Cinq échantillons et six messages finaux ne suffisent pas à estimer une p95 robuste. La dernière ligne inclut le temps de lecture à cadence normale et l'attente finale : elle ne représente pas le seul coût d'inférence. Les 10 inférences incluent celles qui ne produisent pas nécessairement un texte final.

Les cinq sessions ont fermé avec le code 1000 ; aucun timeout ni message d'erreur n'a été observé. Tous les octets ont été acceptés localement par le client et le tampon d'écriture TCP échantillonné après chaque envoi est resté à 0 octet. **Le protocole ne fournit pas d'accusé de réception par trame** : cela ne prouve pas une absence de perte bout en bout, et n'observe ni les tampons serveur ni la file OkHttp Android. Les timestamps de segment peuvent exclure des silences ; la différence avec l'heure d'arrivée est une estimation de calendrier, pas une mesure précise fin de parole → traduction. Le test ne mesure ni la traduction réelle, ni Telegram, ni le rendu de l'overlay Android.

Les résultats numériques sans transcription sont dans [benchmark-real-ws-2026-11-01.json](benchmark-real-ws-2026-11-01.json).

## Reproduire sur un serveur de test indépendant

Prérequis : un environnement `server/.venv` installé, FFmpeg avec filtre `flite`, et un dossier **local déjà complet** d'un modèle faster-whisper. La commande ne doit être lancée que si la charge CPU/RAM disponible permet de préserver le service actif ; le script réserve un port éphémère sur `127.0.0.1`, utilise son propre token fictif, force CPU et ne contacte aucun traducteur.

```bash
cd server
nice -n 19 env PYTHONPATH=. HF_HUB_OFFLINE=1 OMP_NUM_THREADS=1 \
  .venv/bin/python tools/benchmark_real_ws.py \
  --model-dir /chemin/absolu/vers/un/snapshot/local/faster-whisper-tiny \
  --output /tmp/audiolens-real-ws.json
```

Le répertoire doit contenir `model.bin` et les autres fichiers du modèle. Le script ne télécharge pas de modèle ; il échoue si le dossier indiqué ne convient pas. Il crée uniquement des fichiers temporaires synthétiques, dont son propre journal de test, et les supprime en fin d'exécution. Des valeurs absentes, erreurs ou expirations doivent être rapportées, sans les convertir en mesures réussies.

## État de référence du service actif au moment de l'essai

Le 1 novembre à 20:54, le serveur actuel a été lancé depuis `server/` avant les commits de mode clip, de confidentialité et d'instrumentation arrivés ensuite sur `dev`. Sa réponse `/health` indique `small`, `cuda`, `int8`, moteur `openai` et aucune session active au moment du contrôle. La configuration chargée au démarrage indiquait `beam_size=8`, quatre threads CPU, VAD 500 ms / 5 s et un modèle TranslateGemma 4B Q4_K_M sur un service compatible distant. Le service de traduction répondait à son contrôle de santé ; sa disponibilité ne prouve pas son absence de charge.

Le code Python est chargé dans le processus au démarrage, tandis que les fichiers et commits ont changé depuis. La chronologie et les journaux établissent les différences principales, mais **aucun hash complet du code effectivement chargé n'a été conservé** : l'arbre Git actuel ne peut donc pas être présenté comme une copie attestée de ce processus. Le processus est resté en marche pendant l'essai. Les protections de journaux et la télémétrie ajoutées après son lancement ne sont pas présumées actives. Voir [procédure de validation](validation-phone.md) avant toute bascule.

## Priorités d'optimisation à vérifier

1. **Traduction distante et sérialisation du pipeline** — gain potentiellement élevé mais non mesuré ici ; coût et qualité à mesurer sur instance de test et moteur indépendant. Complexité moyenne, risque de qualité ou surcharge du service partagé.
2. **Inférences partielles et finales** — les partiels concurrencent les finaux ; limiter leur fréquence peut réduire la charge. Gain dépendant du contenu, complexité faible à moyenne, risque d'affichage moins réactif.
3. **VAD retraitant le segment entier** — les mesures ci-dessus montrent son coût par trame mais pas son évolution sur une longue phrase. Un VAD incrémental peut réduire le travail répété ; complexité moyenne, risque de segmentation et de mots coupés.

Déplacer la consommation du générateur Whisper hors de la boucle asyncio mérite également un test isolé (réactivité, sémaphore et annulation). Un découplage complet via file bornée demande davantage de précautions sur l'ordre, les surcharges et l'arrêt ; aucune refonte asynchrone n'a été réalisée ici.
