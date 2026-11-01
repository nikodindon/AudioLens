# Dépannage

Ce guide décrit la branche `dev`. Pour l'installation et les limites générales, voir le [README](../README.md). Ne publiez pas de token, clé API, audio privé ou transcription dans un ticket.

## Le serveur ne démarre pas

- Depuis `server/`, vérifiez l'environnement virtuel et le chemin de configuration : `python -m app.main --config config.yaml`.
- `configuration file not found` : créez votre fichier à partir de `config.example.yaml` et remplacez le token d'exemple par une valeur privée d'au moins 16 caractères. `openssl rand -hex 16` génère une valeur adaptée.
- Le premier chargement d'un modèle absent peut nécessiter un téléchargement et de l'espace disque. Le modèle et le périphérique effectifs sont annoncés au démarrage et exposés par `/health`.
- Pour le GPU, vérifiez `nvidia-smi`, le pilote et les bibliothèques installées. Après installation volontaire de l'extra GPU, `source scripts/gpu_env.sh` configure les bibliothèques pour le terminal courant. Sinon, vérifiez le repli CPU et les réglages `whisper.device` / `allow_cpu_fallback`.

## Le téléphone ne joint pas le serveur

1. Vérifiez que le serveur répond sur son ordinateur : `curl http://127.0.0.1:8765/health`.
2. En USB, vérifiez `adb devices`, puis configurez `adb reverse tcp:8765 tcp:8765`. Dans l'application, utilisez `http://localhost:8765` et renseignez le token dans son champ dédié.
3. Sans USB, vérifiez l'adresse de l'interface d'écoute, le chemin réseau privé et les ACL. La politique Android de [network_security_config.xml](../android/app/src/main/res/xml/network_security_config.xml) limite les hôtes autorisés en `ws://` clair. Une autre adresse peut nécessiter une adaptation de l'application ou un accès `wss://` approprié.
4. `/health` vérifie l'état du serveur mais ne prouve pas que le téléphone ou le moteur de traduction externe y accède.

Le client Android envoie le token dans l'en-tête Bearer, pas dans l'URL. N'ajoutez pas `?token=...` à l'adresse Android. La query n'est autorisée côté serveur que si `server.allow_query_token` est explicitement activé.

## Le bandeau reste en attente

- Vérifiez l'état de la connexion dans AudioLens, puis que le serveur est lancé. Après une coupure, arrêtez la session et démarrez une nouvelle capture : la reconnexion automatique n'est pas implémentée.
- Vérifiez l'autorisation de superposition et le consentement MediaProjection. Android peut demander un nouveau consentement à chaque session.
- Vérifiez que l'application source et le contenu autorisent la capture. Un flux protégé, un appel ou certains messages vocaux peuvent produire du silence ; essayez un contenu explicitement autorisé.
- Si le texte source apparaît mais pas sa traduction, vérifiez `translation.engine`, la configuration et la disponibilité du moteur choisi. `none` désactive la traduction.
- Une notification de saturation indique que la capture s'est arrêtée et qu'une partie de l'audio n'a pas été envoyée. Réduisez la charge ou examinez le réseau avant de redémarrer.

## Latence et diagnostic

Les chiffres du [benchmark Whisper historique](benchmark.md) ne représentent pas une latence de bout en bout. Consultez l'[analyse du pipeline](streaming-latency.md) et le [benchmark synthétique](benchmark-streaming-report.md) avant de modifier le modèle ou les seuils.

Le WAV de diagnostic est désactivé par défaut. S'il est activé dans les réglages, l'audio enregistré est privé ; le désactiver ne supprime pas automatiquement les anciens fichiers. L'instrumentation facultative mesure des durées locales, sans synchroniser les horloges du téléphone et du serveur.
