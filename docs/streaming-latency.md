# Analyse de latence du streaming

Analyse statique du pipeline actuel, sans charge injectée dans le serveur utilisé
sur le téléphone. Les résultats ci-dessous ne constituent pas une mesure de la
latence de bout en bout de la session Telegram validée.

## Chemin critique en mode live

1. Chaque trame PCM reçue est ajoutée au segment courant. À chaque trame,
   `Session.process` concatène de nouveau le segment et exécute le VAD sur
   l'ensemble de ce segment. Le coût cumulé grandit donc avec la durée et la
   fréquence des trames ; le VAD est exécuté dans un thread, mais sa réponse est
   attendue avant lecture de la trame suivante.
2. La finalisation attend le silence configuré ou la limite de segment. Des
   partiels peuvent déclencher une inférence environ chaque seconde de segment.
   Ils concurrencent les finaux pour le sémaphore d'inférence partagé.
3. Whisper s'exécute sous un sémaphore global. Sur `dev`, l'appel à
   `model.transcribe` et la consommation de son générateur sont exécutés dans
   un thread ; auparavant, l'itération bloquait la boucle asyncio. La mesure
   `stt_wait` distingue l'attente du sémaphore de l'inférence.
4. Pour un final, le serveur attend ensuite la traduction avant de transmettre
   le résultat et de reprendre la lecture WebSocket de cette session. Le moteur
   HTTP peut attendre jusqu'à 30 secondes. Pendant ces étapes, les trames
   s'accumulent dans les tampons réseau puis éventuellement dans la file Android.
5. Android limite maintenant sa propre file à environ huit secondes de PCM.
   Ce seuil ne mesure pas les tampons intermédiaires ni les données déjà reçues
   par le serveur. La durée maximale de finalisation côté Android est de 60 s.

Le mode clip ne fait pas de partiels : il accumule l'audio jusqu'à `stop`, puis
transcrit et traduit. La mémoire et la durée de finalisation augmentent avec le
clip ; `server.max_audio_seconds` reste le plafond d'entrée.

## Mesures existantes et limites

`docs/benchmark.md` consigne un test antérieur de 68,4 s : modèle `small`, GPU
CUDA/int8, environ 4,78 s d'inférence et RTF 0,070. Ce test n'inclut pas la
capture Android, la segmentation en direct, le réseau, l'attente concurrente,
ni la traduction ; il ne démontre donc pas une latence de sous-titre donnée.
Aucun nouveau benchmark n'a été lancé contre le serveur actif.

## Avant toute refonte asynchrone

- Sur un serveur de test isolé et un jeu audio consenti, mesurer séparément les
  temps VAD, attente d'inférence, génération Whisper complète, traduction, âge
  du segment, profondeur de file et temps fin de parole → final affiché.
- Comparer médiane et p95 sur plusieurs longueurs de trame, langues, silences,
  conditions réseau et sessions concurrentes ; inclure les erreurs et délais
  de traduction, pas uniquement les résultats réussis.
- Poursuivre les tests du VAD incrémental et de l'itération Whisper hors de la
  boucle. Ne découpler réception et inférence qu'avec une file bornée,
  une politique de surcharge explicite, l'ordre des segments et des tests de
  finalisation ; mesurer l'effet sur la session réelle avant déploiement.

## Mesure réelle ultérieure

Un essai WebSocket avec VAD et Whisper réels sur CPU, sur une instance isolée,
est documenté dans [benchmark-real-ws-2026-11-01.md](benchmark-real-ws-2026-11-01.md).
La traduction et la latence Telegram → overlay n'y ont pas été mesurées.
