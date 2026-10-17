# Brief — music_ranger : automatiser l'ajout de musique sur mimosa

## Contexte

- Serveur **mimosa** : Ubuntu 24.04, en ligne de commande uniquement. Les crons et services loguent via `logger` (journald).
- Bibliothèque : `/media/werdeil/internal_drive/Musique`, environ 24 400 fichiers, en ALAC (`.m4a`) avec quelques MP3.
- Lecture : **LMS/Lyrion**, en Docker (`network_mode: host`). Il détecte lui-même les changements de fichiers, donc **ne jamais déclencher de rescan**.
- **mimosa devient la référence** de la bibliothèque. Apple Music, XLD et la synchro Unison manuelle depuis le Mac sont abandonnés.
- La copie de la bibliothèque sur le disque « WD Vincent » (branché au Mac) sert de **sauvegarde**.
- Les scripts d'enrichissement (ReplayGain, paroles, pochette) vivent aujourd'hui dans le projet **lyrion-dashboard**, notamment `embed_lyrics_cron.sh`. Ils tournent chaque nuit en cron et acceptent un dossier en argument.

## Règles de travail

- Réponds en français, de façon concise. Je suis souvent sur mobile.
- **Une étape à la fois.** Montre le plan de chaque étape et attends ma validation avant de l'exécuter.
- **Dry-run avant toute opération destructive** (déplacement, suppression, conversion, modification de crontab ou de systemd).
- Pour chaque changement, prévois un **retour arrière**. Je le préfère à la solution la plus élégante.
- **Ne modifie pas les tags** en dehors de ce que font déjà mes scripts d'enrichissement : pas de retag automatique, pas de MusicBrainz.
- **Un passage complet sur la bibliothèque est long.** Évite-le, travaille sur des périmètres ciblés, et annonce-le comme long quand il est nécessaire.
- Fais un commit git à la fin de chaque étape validée.

## Existant : `music_ranger.py` (dans ce dépôt)

Ce script range la bibliothèque à la façon d'Apple Music. Il fonctionne en dry-run par défaut, avec `--apply` pour exécuter. Ses règles de nommage ont été **validées sur toute la bibliothèque** et ne doivent pas être modifiées sans raison :

- Chemins :
  - `Artiste de l'album/Album/NN Titre.ext`, avec repli sur `Artiste` si l'artiste de l'album est absent ;
  - `Compilations/Album/…` si le flag `cpil` est posé ;
  - `D-NN Titre` pour les albums multi-disques.
- Caractères remplacés par `_` : `/ : ? " | ’ ´` ainsi que `‘ “ ” * < > \`. Un `.` en début de nom est remplacé, et un `.` en fin de nom de dossier aussi.
- Tolérances, pour ne pas renommer l'existant à tort :
  - différences d'encodage des accents (NFC/NFD) ;
  - différences de casse seules ;
  - noms tronqués.
- Les fichiers annexes (`folder.jpg`, `Booklet.pdf`, `.lrc`…) suivent l'album quand il est déplacé.
- Les fichiers parasites (`.DS_Store`, `._*`) sont supprimés avec `--apply`.

Résultat du dernier dry-run complet : 24 406 fichiers en place. Il reste deux écarts, tous deux voulus :

- un renommage dans `Queens of the Stone Age/Villains` : le nom de fichier contient un caractère `\x92` parasite, le tag est correct ;
- un fichier ignoré, `Bootleggers United/EP #9/03 Didi Works Your Body.mp3`, dont le titre est absent des tags. Je le corrigerai à la main.

Il y a aussi 72 fichiers parasites.

## Étapes

### 0. État des lieux (lecture seule)

- Lis `music_ranger.py`.
- Localise les scripts d'enrichissement dans lyrion-dashboard. Note leurs dépendances, leurs arguments et ce que chacun modifie.
- Liste les crons concernés (`crontab -l`, `/etc/cron.*`) et le partage Samba existant.
- Vérifie le contenu de `Musique/Automatically Add to Music.localized`.
- Propose-moi un plan détaillé pour les étapes suivantes.

### 1. Durcir `music_ranger.py`

- **Journal des déplacements** : un fichier horodaté qui enregistre source → cible pour chaque opération.
- **`--undo <journal>`** : rejoue le journal à l'envers.
- **Garde-fou compilation** : si les pistes d'un même album ont un flag `cpil` incohérent, l'album est signalé en CONFLIT et n'est pas éclaté entre plusieurs artistes.
- **Exclusions** : le script ignore l'Inbox et `Automatically Add to Music.localized`.
- **`--since`** : ne traite que les fichiers modifiés depuis le dernier passage. Le filtrage se fait sur les dates du système de fichiers, sans ouvrir les fichiers. La date du dernier passage est gardée dans un fichier d'état.
- **Logs** : sortie via `logger -t music-ranger`, plus un résumé lisible.
- **Tests unitaires** sur les règles de nommage, à partir des cas réels :
  - `?`, `:`, `"`, `|`, `’`, `´`, et `\x92` dans un nom existant ;
  - multi-disques ;
  - compilations ;
  - NFD/NFC ;
  - tags manquants.

### 2. Application réelle sur la bibliothèque

- Dry-run ciblé sur les dossiers concernés, puis `--apply` avec journal.
- Vérification après coup, et commande de retour arrière prête.

### 3. Pipeline d'import

- **Inbox** : `/media/werdeil/internal_drive/Inbox_Musique/`, **hors de la bibliothèque** pour que LMS ne l'indexe pas.
  - Elle contient `_erreurs/` et `_traités/`.
  - Le dossier racine n'est jamais supprimé, même vide.
  - Elle est exposée en partage Samba. Propose-moi la config avant de l'appliquer.
- **Entrées acceptées** : fichiers en vrac et dossiers d'album, aux formats FLAC, ALAC et MP3. Pour tout autre format, demande-moi.
- **Déclenchement** : unité systemd `.path` associée à un service. Le service attend la fin de la copie (taille stable) avant de traiter.
- **Traitement** :
  1. Conversion **FLAC → ALAC** avec ffmpeg, en conservant les tags et la pochette. Contrôle de la durée avec ffprobe avant de valider.
  2. Rangement dans la bibliothèque avec `music_ranger.py`.
  3. Enrichissement avec les scripts existants, appelés **uniquement sur les dossiers cibles**.
- **Après le traitement** :
  - les FLAC d'origine sont déplacés dans `_traités/AAAA-MM-JJ/` et purgés après 7 jours ;
  - les fichiers aux tags insuffisants ou en conflit sont déplacés dans `_erreurs/`, avec une ligne de log.

### 4. Réorganisation après édition de tags

J'éditerai les tags depuis le Mac, via le partage Samba.

- Timer systemd chaque nuit qui lance `music_ranger.py --since --apply` avec journal.
- Lancement manuel possible.

### 5. Crons d'enrichissement de nuit

- Les passer en **hebdomadaire**, et seulement sur les fichiers auxquels il manque quelque chose : gain ou paroles. Le but est surtout de rattraper les paroles publiées après l'import.
- Garde l'ancienne crontab sauvegardée pour pouvoir revenir en arrière.

### 6. Nettoyage

- Une fois l'Inbox opérationnelle, supprime `Musique/Automatically Add to Music.localized`, après avoir vérifié qu'il est vide.

### 7. Sauvegarde vers le Mac

- Écris un script **côté Mac**, que j'installerai moi-même : un rsync **à sens unique** de mimosa (SSH) vers le disque WD Vincent, **sans `--delete`**.
- Il se lance à la main, et si possible automatiquement au branchement du disque.
- Donne-moi les étapes pour désactiver Unison.

### Hors périmètre, pour plus tard

- Migration des scripts d'enrichissement de lyrion-dashboard vers ce dépôt.

**Commence par l'étape 0.**
