# music-librarian

[English](README.md) · **Français**

Range une bibliothèque musicale à la façon d'Apple Music, et y importe la musique déposée dans un dossier d'arrivée. Pensé pour une bibliothèque en ALAC `.m4a` avec quelques MP3, il tourne dans un seul conteneur qui surveille le dossier d'arrivée, range ce qui y arrive et enrichit les tags : ReplayGain, pochette et paroles.

Tout est en dry-run sans `--apply`, et chaque déplacement est journalisé pour pouvoir annuler un passage.

## Ce qu'il fait

- **`organize`** range la bibliothèque en `Artiste de l'album/Album/NN Titre.ext` (repli sur l'artiste), `Compilations/Album/…` si le flag compilation est posé, et `D-NN Titre` pour les albums multi-disques. Les caractères qu'Apple Music remplace (`/ : ? " | ’ ´ * < > \` …) deviennent `_`. L'encodage des accents (NFC/NFD), les différences de casse seules et les noms tronqués sont tolérés, pour ne pas renommer l'existant à tort. Les annexes (`folder.jpg`, livrets, `.lrc`) suivent leur album ; `.DS_Store` et `._*` sont supprimés. Un album dont les pistes ne s'accordent pas sur le flag compilation est signalé en conflit et laissé en place. `--since` ne relit que les dossiers modifiés depuis le dernier passage complet. Les règles complètes sont dans [docs/naming-rules.md](docs/naming-rules.md) (en anglais).
- **`import`** prend les dossiers d'album ou fichiers en vrac déposés dans l'Inbox (FLAC, ALAC, MP3), convertit le FLAC en ALAC avec ffmpeg (tags et pochette conservés, durée contrôlée avec ffprobe), range tout ou rien, et n'enrichit que les dossiers cibles. Les originaux partent dans `_done/AAAA-MM-JJ/`, purgés après 7 jours ; ce qui ne peut pas être rangé part dans `_failed/`.
- **`enrich`** passe sur toute la bibliothèque et ne complète que ce qui manque : ReplayGain mesuré avec ffmpeg, la pochette intégrée quand `folder.jpg` en diffère, et les paroles de LRCLIB et NetEase, plus Musixmatch s'il est activé. Les paroles ne sont écrites que si le titre, l'artiste et la durée du fournisseur correspondent au fichier.
- **`watch`** est le processus permanent du conteneur : il importe ce qui arrive dans l'Inbox une fois la copie terminée, et peut lancer `organize --since` chaque nuit (`--organize-at`) et `enrich` chaque semaine (`--enrich-at "sun 03:00"`, jours en anglais ou en français).
- **`undo JOURNAL`** rejoue le journal d'un passage à l'envers.
- En option, pour une bibliothèque servie par [Lyrion Music Server](https://lyrion.org) (ex-Logitech Media Server), les lectures et notes suivent les fichiers déplacés au lieu de rester orphelines : les déplacements sont reportés dans son `persist.db`. Le plugin Alternative Play Count doit être installé, sa table étant mise à jour aussi (voir [docs/lyrion.md](docs/lyrion.md), en anglais).

## Lancement

Docker est la voie prévue. ffmpeg est dans l'image, et les tests tournent au build : une image cassée n'est jamais produite.

```bash
cp .env.example .env      # chemins de la bibliothèque, de l'Inbox et (facultatif) de Lyrion
mkdir state               # journaux, dernier passage et verrou ; à créer avant le premier lancement
docker compose build
docker compose up -d      # watch --apply, rangement à 02:00, enrichissement le dimanche à 03:00
journalctl -t music-librarian -f
```

Pour utiliser l'image publiée plutôt que de la construire, remplacer la section `build:` et la ligne `image:` de `docker-compose.yml` par `image: ghcr.io/werdeil/music-librarian:0.1` (ou `latest`), puis `docker compose pull`. Le tag `dev` suit `main` entre deux versions, sans autre test que la CI. Les images sont construites pour amd64 et arm64.

La bibliothèque et l'Inbox doivent se trouver sous le dossier unique de `MUSIC_LIBRARIAN_VOLUME`, monté au même chemin que sur l'hôte : les déplacements restent des renommages et les chemins des journaux valent des deux côtés.

Lancements ponctuels, en dry-run sans `--apply` :

```bash
docker compose run --rm music-librarian organize --since
docker compose run --rm music-librarian import
docker compose run --rm music-librarian enrich
docker compose run --rm music-librarian undo /state/journal/{date}.jsonl --apply
```

Sans Docker : Python 3.12, ffmpeg dans le `PATH` et `pip install .` (`pip install -r requirements.txt .` pour les versions testées), puis `music-librarian {commande} --help`.

## Configuration

| Variable | Rôle |
|---|---|
| `MUSIC_LIBRARIAN_ROOT` | Racine de la bibliothèque (`--root`) |
| `MUSIC_LIBRARIAN_INBOX` | Dossier de dépôt, hors de la bibliothèque pour qu'un serveur de musique ne l'indexe pas (`--inbox`) |
| `MUSIC_LIBRARIAN_STATE` | Journaux, date du dernier passage et verrou (`--state-dir` ; `/state` dans l'image, `~/.local/state/music-librarian` sinon) |
| `LYRION_PERSIST_DB`, `LYRION_MUSIC_ROOT` | `persist.db` de Lyrion et racine de la bibliothèque vue par Lyrion ; sans eux, l'historique n'est pas reporté |
| `LYRICS_PROVIDERS` | Fournisseurs de paroles essayés dans l'ordre (défaut `lrclib,netease`, voir ci-dessous) |
| `MUSIXMATCH_TOKEN`, `LRCLIB_TIMEOUT` | Réglages facultatifs des fournisseurs |

Le fichier compose lit ses propres variables dans `.env` ; `.env.example` les documente.

### Fournisseurs de paroles

[LRCLIB](https://lrclib.net) est une base ouverte avec une API publique. NetEase Cloud Music et Musixmatch sont interrogés par les points d'accès de leurs propres applications, qui ne sont pas des API publiques : ils peuvent changer ou bloquer les requêtes à tout moment, et leur usage peut contrevenir à leurs conditions d'utilisation. Musixmatch est donc désactivé par défaut ; l'ajouter à `LYRICS_PROVIDERS` (par exemple `lrclib,musixmatch,netease`) si vous l'acceptez. NetEase peut être retiré de la même façon.

## Documentation

En anglais :

- [Règles de nommage](docs/naming-rules.md) : construction des chemins cibles, et quand un fichier est considéré en place
- [Report de l'historique Lyrion](docs/lyrion.md)
- [Architecture](docs/architecture.md) : organisation du paquet, pour les contributeurs

## Développement

```bash
pip install -r requirements-dev.txt -e .
python -m pytest -q    # demande ffmpeg
pylint src tests
```

Voir [CONTRIBUTING.md](CONTRIBUTING.md) avant d'ouvrir une pull request, et les [releases](https://github.com/werdeil/music-librarian/releases) pour les changements entre versions (en anglais).

## Licence

[MIT](LICENSE)
