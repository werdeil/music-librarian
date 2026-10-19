# music-librarian

[English](README.md) · **Français**

Range une bibliothèque musicale à la façon d'Apple Music, et y importe la musique déposée dans un dossier d'arrivée. Pensé pour une bibliothèque en ALAC `.m4a` avec quelques MP3, il tourne dans un seul conteneur qui surveille le dossier d'arrivée, range ce qui y arrive et enrichit les tags : ReplayGain, pochette et paroles.

Tout est en dry-run sans `--apply`, et chaque déplacement est journalisé pour pouvoir annuler un passage.

## Ce qu'il fait

- **`range`** range la bibliothèque en `Artiste de l'album/Album/NN Titre.ext` (repli sur l'artiste), `Compilations/Album/…` si le flag compilation est posé, et `D-NN Titre` pour les albums multi-disques. Les caractères qu'Apple Music remplace (`/ : ? " | ’ ´ * < > \` …) deviennent `_`. L'encodage des accents (NFC/NFD), les différences de casse seules et les noms tronqués sont tolérés, pour ne pas renommer l'existant à tort. Les annexes (`folder.jpg`, livrets, `.lrc`) suivent leur album ; `.DS_Store` et `._*` sont supprimés. Un album dont les pistes ne s'accordent pas sur le flag compilation est signalé en conflit et laissé en place. `--since` ne relit que les dossiers modifiés depuis le dernier passage complet.
- **`import`** prend les dossiers d'album ou fichiers en vrac déposés dans l'Inbox (FLAC, ALAC, MP3), convertit le FLAC en ALAC avec ffmpeg (tags et pochette conservés, durée contrôlée avec ffprobe), range tout ou rien, et n'enrichit que les dossiers cibles. Les originaux partent dans `_traités/AAAA-MM-JJ/`, purgés après 7 jours ; ce qui ne peut pas être rangé part dans `_erreurs/`.
- **`enrich`** passe sur toute la bibliothèque et ne complète que ce qui manque : ReplayGain (r128gain), la pochette intégrée quand `folder.jpg` en diffère, et les paroles de LRCLIB, Musixmatch et NetEase. Les paroles ne sont écrites que si le titre, l'artiste et la durée du fournisseur correspondent au fichier.
- **`watch`** est le processus permanent du conteneur : il importe ce qui arrive dans l'Inbox une fois la copie terminée, et peut lancer `range --since` chaque nuit et `enrich` chaque semaine.
- **`undo JOURNAL`** rejoue le journal d'un passage à l'envers.
- En option, pour une bibliothèque servie par [Lyrion Music Server](https://lyrion.org) (ex-Logitech Media Server), les lectures et notes suivent les fichiers déplacés au lieu de rester orphelines : les déplacements sont reportés dans son `persist.db`. Le plugin Alternative Play Count doit être installé, sa table étant mise à jour aussi (voir `lyrion.py`).

## Lancement

Docker est la voie prévue. ffmpeg et r128gain sont dans l'image, et les tests tournent au build : une image cassée n'est jamais produite.

```bash
cp .env.example .env      # chemins de la bibliothèque, de l'Inbox et (facultatif) de Lyrion
mkdir state               # journaux, dernier passage et verrou ; à créer avant le premier lancement
docker compose build
docker compose up -d      # watch --apply, rangement à 02:00, enrichissement le dimanche à 03:00
journalctl -t music-librarian -f
```

La bibliothèque et l'Inbox doivent se trouver sous le dossier unique de `MUSIC_LIBRARIAN_VOLUME`, monté au même chemin que sur l'hôte : les déplacements restent des renommages et les chemins des journaux valent des deux côtés.

Lancements ponctuels, en dry-run sans `--apply` :

```bash
docker compose run --rm music-librarian range --since
docker compose run --rm music-librarian import
docker compose run --rm music-librarian enrich
docker compose run --rm music-librarian undo /state/journal/{date}.jsonl --apply
```

Sans Docker : Python 3.12, ffmpeg, `pip install -r requirements.txt` et r128gain 1.0.7 dans le `PATH` (ou dans `R128GAIN`), puis `./music-librarian {commande} --help`.

## Configuration

| Variable | Rôle |
|---|---|
| `MUSIC_LIBRARIAN_ROOT` | Racine de la bibliothèque (`--root`) |
| `MUSIC_LIBRARIAN_INBOX` | Dossier de dépôt, hors de la bibliothèque pour qu'un serveur de musique ne l'indexe pas (`--inbox`) |
| `MUSIC_LIBRARIAN_STATE` | Journaux, date du dernier passage et verrou (`--state-dir` ; `/state` dans l'image) |
| `LYRION_PERSIST_DB`, `LYRION_MUSIC_ROOT` | `persist.db` de Lyrion et racine de la bibliothèque vue par Lyrion ; sans eux, l'historique n'est pas reporté |
| `LYRICS_PROVIDERS` | Fournisseurs de paroles essayés dans l'ordre (défaut `lrclib,musixmatch,netease`) |
| `MUSIXMATCH_TOKEN`, `LRCLIB_TIMEOUT` | Réglages facultatifs des fournisseurs |
| `R128GAIN` | Exécutable r128gain |

Le fichier compose lit ses propres variables dans `.env` ; `.env.example` les documente.

## Développement

```bash
pip install -r requirements-dev.txt
python -m pytest -q tests     # demande ffmpeg
pylint *.py music-librarian tests
```

## Licence

[MIT](LICENSE)
