# Latence — benchmark synthétique isolé

## Mesure et limites

Mesure effectuée le 1 novembre 2026 sur le PC de développement, sans accéder au
serveur actif, au téléphone, au GPU ni à une conversation privée. L'outil
`server/tools/benchmark_streaming.py` instancie `Session` dans son propre
processus, avec VAD, STT et traduction simulés et des trames PCM de silence.
Il ne lance pas de serveur HTTP et ne mesure ni WebSocket réel, ni OkHttp, ni
capture ou overlay. La file mesurée est celle du générateur du benchmark, pas la
file interne d'OkHttp ou du serveur réseau. Les délais sont imposés aux faux
composants ; ces résultats valident surtout l'outil et illustrent l'accumulation,
pas les performances réelles de Whisper ou de Telegram.

Commande (depuis `server/`) :

```bash
PYTHONPATH=. .venv/bin/python tools/benchmark_streaming.py \
  --runs 5 --frames 12 --frame-ms 100 \
  --json /tmp/audiolens-benchmark.json --markdown /tmp/audiolens-benchmark.md
```

Les données brutes de cette exécution sont dans
[benchmark_streaming_synthetic.json](benchmark_streaming_synthetic.json). Les
valeurs p95 portent sur cinq exécutions ou, pour les étapes, sur les événements
collectés ; cinq échantillons sont insuffisants pour une estimation robuste.
Le RTF ici est le temps mural total divisé par la durée audio synthétique. Il
inclut le rythme d'envoi, il ne représente donc pas uniquement le coût de calcul.

| Mesure | Normal, médiane / p95 | Ralenti, médiane / p95 |
|---|---:|---:|
| Durée de session (ms) | 1208,46 / 1208,61 | 2108,38 / 2108,68 |
| RTF mural | 1,007 / 1,007 | 1,757 / 1,757 |
| VAD simulé (ms) | 5,19 / 5,32 | 150,47 / 150,57 |
| Attente sémaphore simulé (ms) | 0,006 / 0,009 | 0,006 / 0,009 |
| STT simulé (ms) | 5,25 / 5,27 | 150,24 / 150,39 |
| Traduction simulée (ms) | 5,51 / 5,88 | 150,42 / 150,49 |
| Pic de file du générateur (trames) | 1 / 1 | 4 / 4 |
| Erreurs / délais dépassés | 0 / 0 | 0 / 0 |

## Instrumentation facultative

Serveur : définir `AUDIO_LENS_METRICS=1` **au démarrage d'une instance de test**.
Les logs `audiolens.metrics` enregistrent seulement nom d'étape et durée
monotone locale : `vad`, `stt_wait`, `stt_inference`, `stt_total`,
`translation`, `frame_process` et `ws_send`. Les métriques sont désactivées par
défaut. Elles incluent le temps d'attente et d'exécution selon leur nom ;
`stt_total` comprend chargement/résolution, attente et inférence, tandis que
`stt_inference` inclut la consommation du générateur. Les mesures par trame
peuvent augmenter le volume de journaux quand elles sont activées.

Android : l'option « Mesurer les délais techniques » est désactivée par défaut
et s'applique à la prochaine capture. Les logs `AudioLensMetrics` donnent un
échantillon de durée de mise en file et de profondeur OkHttp toutes les cinq
secondes, ainsi que le délai local entre réception d'un final et mise à jour du
bandeau. La durée de remplissage du tampon audio, les files réseau et l'attente
serveur avant lecture ne sont pas directement mesurées. Une mise à jour de
vue n'est pas une preuve du moment exact de rendu à l'écran. Les horloges
monotones des deux machines ne sont pas comparables ; aucune latence complète
parole → affichage ne peut être déduite en additionnant ces seules mesures.

## Roadmap, à vérifier sur une instance isolée

| Piste | Gain attendu | Complexité / risque | Validation proposée |
|---|---|---|---|
| VAD incrémental | potentiellement élevé pour longues phrases et petites trames | moyenne / risque de coupure de mots | corpus synthétique puis audio consenti, comparer segments et p95 |
| Consommer tout le générateur Whisper dans un thread | réactivité asyncio potentiellement améliorée | faible à moyenne / concurrence et annulation | tester heartbeat, sémaphore et résultats identiques |
| Limiter les partiels | moins d'inférences concurrentes | faible / affichage initial plus tardif | comparer nombre de partiels et latence finale |
| Ajuster silence et segments | final plus rapide selon contenu | faible / phrases tronquées | comparer ponctuation et segments multilingues |
| Optimiser requêtes traduction | potentiellement élevé si service distant lent | moyenne / qualité et délais d'erreur | mesurer médiane/p95 et qualité sur corpus autorisé |
| Découpler réception et inférence | protège la réception en charge | élevée / ordre, mémoire et perte audio | file bornée, surcharge visible, arrêt et tests réseau avant déploiement |

Aucun gain chiffré n'est revendiqué pour ces pistes avant mesure réelle sur une
instance indépendante et validation du parcours Telegram.
