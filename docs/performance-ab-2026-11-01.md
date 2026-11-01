# Expérimentations de performance A/B — 1 novembre 2026

## Cadre et portée

Comparaisons isolées sur CPU Intel i5-8300H, `nice 19`, un thread, modèle `faster-whisper-tiny` local, `int8`, sans téléchargement, GPU, serveur actif ni TranslateGemma partagé. La parole anglaise est synthétisée localement par FFmpeg/libflite ; aucun message privé n'est utilisé. Versions : faster-whisper 1.2.1, CTranslate2 4.8.4, onnxruntime 1.31.1, websockets 17.4, FFmpeg 6.1.1.

La référence applicative est `f7a5d7f`. Les outils étendus sont dans `e0226f2`, l'optimisation Whisper dans `4c3f7c6`. Les captures JSON contiennent des hachages de texte synthétique et non les transcriptions : [résultats bruts](performance-ab-2026-11-01.json). Les mesures concernent le CPU, pas le GPU Pascal ni la traduction russe → français. Les p95 sur 20 répétitions restent fragiles ; les variations de charge et de cache n'ont pas été éliminées.

## Whisper : générateur dans la boucle ou dans un thread

Le générateur de faster-whisper exécute effectivement du travail lorsqu'on l'itère. Dans l'ancienne version, l'appel initial est dans un thread, mais l'itération bloque la boucle asyncio. Une comparaison sur une phrase synthétique de 3 s, beam 1, 20 paires avec ordre alterné après un échauffement, donne :

| Indicateur | Avant médiane / p95 | Après médiane / p95 |
|---|---:|---:|
| Temps mural d'inférence (ms) | 833,95 / 910,29 | 781,26 / 875,86 |
| CPU du processus par essai (ms) | 825,98 / 909,92 | 790,09 / 886,82 |
| Retard maximal du heartbeat asyncio par essai (ms) | 824,02 / 900,37 | 0,575 / 2,073 |
| WER sur la phrase synthétique | 0 / 0 | 0 / 0 |

La disponibilité de la boucle s'améliore fortement dans cet essai ; la différence de temps d'inférence ou de CPU ne doit pas être attribuée avec certitude à l'optimisation sur ce seul corpus. Le heartbeat est un indicateur local de réactivité, pas une mesure de latence utilisateur. Les 20 paires produisent exactement la même séquence de segments, texte et bornes. Un seul énoncé et un seul modèle ne prouvent pas l'absence de régression linguistique générale.

Le code retenu exécute l'appel **et** la consommation du générateur dans un thread. Un `asyncio.shield` protège le travail natif et, en cas d'annulation, le sémaphore reste occupé jusqu'à sa fin. Des tests couvrent le thread, l'ordre du résultat, l'annulation concurrente et la libération après erreur. Pour revenir au comportement initial, annuler `a43561b` puis `4c3f7c6` sur une branche de test et relancer les tests ; ne rien appliquer au service actif sans autorisation.

## WebSocket : avant / après

Deux copies isolées, référence puis optimisation, utilisent les mêmes cinq courtes phrases synthétiques répétées quatre fois : 20 sessions par version, trames 50 ms, silence final 200 ms, live sans partiels, `tiny` CPU/int8, beam 5, sans traduction. Les séries ne sont pas alternées : comparer leurs variations avec prudence.

| Indicateur | Avant médiane / p95 | Après médiane / p95 |
|---|---:|---:|
| STT (ms) | 774,70 / 964,72 | 780,33 / 910,61 |
| `stop` → fermeture (ms) | 776,27 / 966,49 | 782,31 / 912,58 |
| RTF mural incluant l'envoi cadencé | 1,479 / 1,595 | 1,478 / 1,543 |
| CPU total du processus serveur (20 sessions, chargement inclus) | 21,010 s | 19,052 s |

Les 20 paires de séquences `(id, début, fin, hash du texte)` sont identiques ; aucun identifiant dupliqué ou inversé, timeout ou erreur n'a été observé. Tous les octets ont été acceptés par le client local, mais aucun accusé de réception audio par trame n'existe : cela ne prouve pas l'absence de perte bout en bout. Le tampon d'écriture échantillonné ne voit pas toutes les files intermédiaires. La médiane de finalisation n'a pas diminué ; aucune amélioration fiable de latence utilisateur n'est revendiquée ici.

Le banc accepte désormais `--length short|medium|long`, `--silence-ms`, `--frame-ms 20|50|100`, `--mode live|clip`, `--partials` et `--runs`. Deux essais de fonctionnement supplémentaires, un seul chacun, ont utilisé une phrase moyenne avec trames 20 ms et partiels (trois partiels observés), puis un clip long en trames 100 ms (`final` puis `done`). Aucun p95 fiable ne peut être tiré d'un seul essai. La matrice complète à 20 répétitions par combinaison prendrait un temps important à cadence réelle ; elle n'a pas été lancée sur cette machine partagée dont le swap était presque plein.

## VAD : expérimentation isolée, non intégrée

Le VAD actuel repasse sur tout le préfixe à chaque trame. Un candidat de recherche maintient les états ONNX `h`, `c` et le contexte de 64 échantillons, tout en recalculant la dernière fenêtre : l'implémentation faster-whisper remet à zéro la fin de la dernière fenêtre lors de son prétraitement. Cette observation est spécifique à la version installée ; le candidat n'est pas utilisé par le serveur.

| Trame / silence synthétique | Répétitions | CPU total préfixes : complet médiane / p95 | CPU total : candidat médiane / p95 | Désaccords de seuil |
|---|---:|---:|---:|---:|
| 100 ms / 200 ms | 20 | 313,45 / 369,91 ms | 44,61 / 60,94 ms | 0 |
| 100 ms / 1 000 ms | 20 | 259,76 / 274,25 ms | 37,98 / 41,30 ms | 0 |
| 20 ms / 200 ms | 3 | 1 554,04 / 1 576,04 ms | 108,66 / 110,74 ms | 0 |
| 50 ms / 200 ms | 3 | 602,51 / 639,56 ms | 69,46 / 73,97 ms | 0 |

Le candidat a produit les mêmes probabilités observées et les mêmes drapeaux finaux sur ce corpus et cette version. Les mesures 20 et 50 ms ne comptent que trois essais, insuffisants pour une p95 fiable. Le total CPU comprend les appels sur chaque préfixe d'un flux de 5 s ; il ne s'agit pas de latence par trame. À 100 ms / silence court, les médianes par préfixe complet passent d'environ **1,40 ms** à la première seconde à **11,07 ms** à la cinquième (médiane des médianes de chaque essai).

Le gain potentiel est important, mais les comparaisons ne couvrent ni des langues variées, ni chevauchements, musique, coupures, réinitialisations de segment, ni une évolution future de Silero. Elles ne démontrent pas encore l'équivalence des segments complets en production : le remplacement du VAD est donc différé.

## Reproduire sans utiliser les services actifs

Depuis `server/`, avec un modèle tiny **déjà présent** et FFmpeg incluant `flite`, définir `MODEL_DIR` vers son dossier absolu. Ne pas lancer ces tests si la charge système menace le service actif.

```bash
nice -n 19 env PYTHONPATH=. HF_HUB_OFFLINE=1 OMP_NUM_THREADS=1 \
  .venv/bin/python tools/benchmark_whisper_ab.py --model-dir "$MODEL_DIR" \
  --runs 20 --output /tmp/whisper-ab.json
nice -n 19 env PYTHONPATH=. OMP_NUM_THREADS=1 \
  .venv/bin/python tools/benchmark_vad_ab.py --runs 20 --frame-ms 100 \
  --silence-ms 200 --output /tmp/vad-ab.json
nice -n 19 env PYTHONPATH=. HF_HUB_OFFLINE=1 OMP_NUM_THREADS=1 \
  .venv/bin/python tools/benchmark_real_ws.py --model-dir "$MODEL_DIR" \
  --runs 20 --frame-ms 50 --silence-ms 200 --length short --output /tmp/ws.json
```

Pour comparer WebSocket, exécuter la dernière commande dans deux copies isolées : `git archive f7a5d7f server` pour la référence et `git archive 4c3f7c6 server` pour l'optimisation. Utiliser **le même outil** `e0226f2:server/tools/benchmark_real_ws.py` dans les deux copies, le même environnement Python, le même modèle et les mêmes arguments. La commande crée et arrête uniquement son propre serveur `127.0.0.1` sur port éphémère ; ne pas cibler 8765. Les copies par archive ne contiennent pas les modifications locales non commitées.

## Protocole proposé pour TranslateGemma — non exécuté

Utiliser une instance et une copie du modèle indépendantes, sans solliciter le service partagé. Préparer des phrases russes synthétiques courtes, moyennes et longues ainsi que leurs références françaises, avec ambiguïtés et ponctuation ; ne pas utiliser de messages personnels. Évaluer au moins 20 requêtes par classe après échauffement, en alternant les paramètres et en notant modèle, quantification, matériel, `max_tokens`, prompt, concurrence et version.

Mesurer durée monotone de requête, médiane, p95, timeouts et erreurs, puis relire la fidélité, les omissions, hallucinations, langue de sortie et troncatures. Comparer la qualité et le temps ensemble ; ne pas journaliser tokens, en-têtes ou contenu privé. Le service actuel peut être occupé : cette expérimentation exige une instance indépendante et une fenêtre de ressources autorisée.
