# Compatibilité de capture Android

`AudioPlaybackCapture` dépend du système, de l'application source, du type de flux et des protections du contenu. Le tableau suit les éléments connus ; il ne constitue pas une garantie générale.

| Application / contenu | Observation | Limite |
|---|---|---|
| Telegram / vidéo | Capture audio observée lors d'un test antérieur ; une vidéo parlée en russe a également été transcrite et traduite en français lors d'une validation manuelle du projet. | Résultat lié au contenu, à l'appareil et aux versions testés ; les changements récents de streaming restent à valider sur appareil. |
| Telegram / message vocal | À tester. | Le chemin de lecture et les autorisations peuvent varier. |
| WhatsApp / vocal ou vidéo | À tester. | Pas de validation générale documentée. |
| YouTube, Instagram, TikTok et autres | À tester. | Ne pas déduire la compatibilité de l'application entière d'un seul média. |
| Appels, certains flux VoIP, contenus protégés | Capture souvent indisponible ou limitée. | Android, l'application source ou le contenu peuvent refuser la capture. |

Les observations précédentes concernent un appareil Android 15 ; voir [device.md](device.md). Pour ajouter une entrée, noter la version d'Android et de l'application, le type de contenu autorisé, la date et le résultat, sans publier de messages, données privées ou identifiants d'appareil.
