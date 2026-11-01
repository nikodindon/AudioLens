# Compatibilite capture Android (Phase 5)

Etat : application de capture installee sur le telephone connecte (realme RMX3834, Android 15 / API 35). Tests manuels Telegram/WhatsApp a faire et a noter ici avec le WAV debug (emplacement interne de l app).

| App | Type de contenu | Audio capture (AudioPlaybackCapture) | Notes |
|---|---|---|---|
| Telegram | vocal | a tester | note RMS / fichier WAV audible |
| Telegram | video | oui | telephone XGROKJCEEQN7ZHF6, Android 15, capture 45.6 s, RMS 313, WAV PCM valide |
| WhatsApp | vocal | a tester | mode ecouteur vs haut-parleur |
| WhatsApp | video | a tester | |
| YouTube | media | probable | usage MEDIA attendu capturable |
| Instagram/TikTok | media | a tester | |

TODO: executer la capture pour chaque cas et renseigner RMS non nul, taille du WAV debug et verifie que le WAV est audible.
