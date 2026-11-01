# Appareil de test

Relevé le 2026-11-01 via `adb`.

| Propriété | Valeur |
|---|---|
| Modèle | realme RMX3834 |
| Fabricant | realme |
| Version Android | 15 |
| API level | 35 |
| ABI | arm64-v8a |
| Écran | 720 × 1600, densité 320 dpi |
| Débogage USB | activé (appareil vu par `adb devices`) |

## Couverture de test

Le téléphone de ce relevé est en **Android 15 / API 35** ; le projet compile et cible actuellement l’API 36.
La branche `minSdk 30` (Android 11) n'est **pas** testable sur cet appareil ;
le code la gère par des vérifications `Build.VERSION.SDK_INT` :

| Comportement | API 30–32 | 33 | 34–35 | Testé ici |
|---|---|---|---|---|
| Permission `POST_NOTIFICATIONS` | absente | runtime | runtime | API 35 seulement ; l’affichage dépend de l’autorisation |
| `FOREGROUND_SERVICE_MEDIA_PROJECTION` | non déclarée | non déclarée | déclarée + type | API 35 |
| Service au premier plan avant `getMediaProjection()` | pris en charge | pris en charge | requis par le parcours actuel | API 35 |
| Consentement MediaProjection à chaque session | oui | oui | oui | API 35 |
| Overlay `TYPE_APPLICATION_OVERLAY` | oui | oui | oui + restrictions OEM | API 35 |
| Cleartext `ws://` via config réseau | selon la config | selon la config | selon la config | hôtes explicitement autorisés uniquement |

Ces observations sont historiques et ne remplacent pas les tests des changements actuels sur appareil. Ne publiez pas de captures ou journaux contenant des données privées.

## Connexion serveur en développement

```bash
adb reverse tcp:8765 tcp:8765   # le téléphone joint ws://localhost:8765/ws
```

Pour valider le chemin réel Tailscale, utiliser l'IP `100.x.y.z` ou le nom
MagicDNS du serveur, avec le serveur écoutant sur cette interface et une configuration réseau Android autorisant l’adresse ou un accès `wss://` adapté.
