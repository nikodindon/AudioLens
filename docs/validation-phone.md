# Préparer une validation sur téléphone et un retour arrière

Cette procédure est une préparation : elle ne constitue pas une autorisation de redémarrer le serveur, installer un APK, modifier les permissions ou lancer une capture sur l'appareil. Ne pas exposer de token, clé ou contenu privé dans les journaux partagés.

## Avant toute bascule

1. Noter la branche et les commits avec `git status --short --branch` et `git log -5 --oneline` ; conserver les modifications locales non commitées, sans reset ni checkout destructif.
2. Identifier le service actif par `/health` et relever son processus et sa configuration effective sans publier de secret. Sauvegarder de manière privée et à permissions restreintes les fichiers de configuration nécessaires, **hors dépôt** ; ne jamais les commiter. Noter l'adresse et les versions des moteurs sans partager leurs identifiants sensibles.
3. Le processus actuel a été lancé avant plusieurs changements et aucun hash complet de son code chargé n'a été enregistré. Les fichiers de travail actuels ne sont donc pas une sauvegarde exacte du processus. Tant qu'il tourne, il reste la référence ; ne pas promettre un retour à l'identique sans reconstituer et vérifier séparément une copie connue du code et de sa configuration.
4. Vérifier les APK présents et conserver la version fonctionnelle. Les paquets `dev.audiolens` et `dev.audiolens.dev` existaient tous deux au contrôle du 1 novembre : une nouvelle installation avec `dev.audiolens.dev` mettrait à jour **la variante Dev existante**, même si elle ne touche pas à `dev.audiolens`. Pour préserver aussi la Dev actuelle, préparer une troisième variante avec un identifiant distinct et vérifier son manifeste avant toute installation.
5. Compiler et tester l'APK hors ligne ; lire son identifiant avec `aapt dump badging`. Pour sauvegarder un APK installé ou interroger l'appareil, choisir une procédure adaptée, en lecture seule et avec accord. Ne pas conserver ni partager des fichiers privés extraits du téléphone.

## Validation après autorisation explicite

- Sur une instance de test indépendante, avec copie de configuration privée et modèles disponibles, vérifier `/health`, le moteur de traduction et un flux synthétique. Ne pas démarrer de second modèle GPU ou charger le traducteur partagé sans accord et sans vérifier la charge.
- Installer uniquement le paquet convenu, après avoir confirmé son identifiant et si l'opération remplace ou non un paquet existant. Ne pas désinstaller ni réinitialiser l'application de référence. Configurer séparément la nouvelle application : les permissions et préférences sont propres à chaque identifiant.
- Avec du contenu autorisé : démarrer, accorder le consentement Android, sélectionner Telegram, lancer une vidéo, vérifier transcription et traduction française, déplacer l'overlay et vérifier sa position après une nouvelle session.
- Vérifier l'arrêt depuis l'application et la notification, puis parler jusqu'à l'arrêt pour vérifier la réception du dernier segment. Tester ensuite, si autorisé, une coupure réseau contrôlée et le signalement, puis le mode clip et sa finalisation. Ne pas publier le contenu des vidéos ou tokens.
- Relever les conditions et mesures sans contenu privé ; comparer au parcours antérieur. Une compilation ou un `/health` ne démontre pas la réussite de la capture réelle.

## Retour arrière

Un retour reproductible nécessite une copie **identifiée et vérifiée à l'avance** du serveur de référence, de sa configuration privée, des modèles et de l'APK ciblé. Préparer ces éléments avant d'arrêter quoi que ce soit ; les conserver hors du dépôt. Après accord spécifique, arrêter seulement l'instance à remplacer et relancer la copie identifiée avec sa configuration, puis vérifier `/health` et le parcours sur appareil. Si la sauvegarde du code réellement chargé n'a pas pu être attestée, le retour exact reste incertain : préserver le processus actuel jusqu'à validation d'une alternative.
