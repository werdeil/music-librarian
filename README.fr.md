# music-librarian

[English](README.md) · **Français**

Range une bibliothèque musicale à la façon d'Apple Music, et y importe la musique déposée dans un dossier d'arrivée. Pensé pour une bibliothèque en ALAC `.m4a` avec quelques MP3, il tourne dans un seul conteneur qui surveille le dossier d'arrivée, range ce qui y arrive et enrichit les tags : ReplayGain, pochette et paroles. Il peut aussi découper les compositeurs et artistes qui regroupent plusieurs noms, sous la forme séparée par `;` que comprend Lyrion, et unifier les noms d'un même artiste écrits de plusieurs façons.

Tout est en dry-run sans `--apply`, et chaque déplacement est journalisé pour pouvoir annuler un passage.

## Ce qu'il fait

- **`organize`** range la bibliothèque en `Artiste de l'album/Album/NN Titre.ext` (repli sur l'artiste), `Compilations/Album/…` si le flag compilation est posé, et `D-NN Titre` pour les albums multi-disques. Les caractères qu'Apple Music remplace (`/ : ? " | ’ ´ * < > \` …) deviennent `_`. L'encodage des accents (NFC/NFD), les différences de casse seules et les noms tronqués sont tolérés, pour ne pas renommer l'existant à tort, sauf si un dossier écrit exactement comme la cible existe déjà à côté de la variante : ses fichiers le rejoignent alors. Les annexes (`folder.jpg`, livrets, `.lrc`) suivent leur album ; `.DS_Store` et `._*` sont supprimés. Un album dont les pistes ne s'accordent pas sur le flag compilation est signalé en conflit et laissé en place. Les fichiers `.m4a` dont l'index (atome `moov`) est placé après l'audio sont réécrits avec l'index en tête, sans réencodage, pour que Lyrion les diffuse en `alc` au lieu de les transcoder en `alcx`. `--since` ne relit que les dossiers modifiés depuis le dernier passage complet. Les règles complètes sont dans [docs/naming-rules.md](docs/naming-rules.md) (en anglais).
- **`import`** prend les dossiers d'album ou fichiers en vrac déposés dans l'Inbox (FLAC, ALAC, MP3), convertit le FLAC en ALAC avec ffmpeg (tags et pochette conservés, durée contrôlée avec ffprobe), range tout ou rien, et n'enrichit que les dossiers cibles. Les originaux partent dans `_done/AAAA-MM-JJ/`, purgés après 7 jours ; ce qui ne peut pas être rangé part dans `_failed/`.
- **`enrich`** passe sur toute la bibliothèque et ne complète que ce qui manque ou s'écarte de la règle : genres alignés sur une liste validée (voir plus bas), ReplayGain mesuré avec ffmpeg, la pochette intégrée quand `folder.jpg` en diffère, et les paroles de LRCLIB et NetEase, plus Musixmatch s'il est activé. Les paroles ne sont écrites que si le titre, l'artiste et la durée du fournisseur correspondent au fichier. Une piste cherchée sans succès n'est recherchée à nouveau qu'après 30 jours (`LYRICS_RETRY_DAYS`), sauf si son artiste, son titre ou son album change. Une simulation liste les pistes dont elle chercherait les paroles, sans les chercher. `--no-lyrics` laisse les paroles de côté, de loin le plus long, et `--no-genres` les genres ; un tel passage ne remplace pas celui de la semaine.
- **`scan`** lit les métadonnées de toute la bibliothèque dans un cache du dossier d'état (`metadata.db`), puis affiche le nombre de pistes par genre. `organize`, `enrich`, `import`, `split` et `dedupe` lisent les tags à travers ce cache : après une lecture complète (une vingtaine de minutes pour 25 000 pistes sur disque dur), un passage n'ouvre que les fichiers dont la taille ou les dates ont changé, et un dry-run suivi de `--apply` ne lit la bibliothèque qu'une fois. Les fichiers disparus de la bibliothèque sortent du cache à chaque `scan` et à chaque passage complet d'`enrich`. `scan --force` relit tous les fichiers quel que soit le contenu du cache, comme `enrich --rescan` avant d'enrichir.
- **`split`** réécrit les tags compositeur et artiste comme `John Lennon/Paul McCartney` en `John Lennon; Paul McCartney`, le séparateur sur lequel Lyrion découpe. Une virgule ou une barre oblique ne découpe que si la bibliothèque le confirme : un crédit qui est l'artiste d'un album quelque part (`AC/DC`, `Earth, Wind & Fire`) est gardé, un crédit dont tous les morceaux sont des noms vus seuls est découpé, et tout autre attend une décision dans `state/split-decisions.txt` (`split`, `keep`, ou les noms à écrire). `split --review` les présente un par un avec une suggestion qu'Entrée accepte, peut s'arrêter et reprendre à tout moment, et saute les crédits qu'une réponse précédente règle déjà. Les artistes d'album qui ressemblent à une collaboration de noms connus (`Iggy Pop, Muse`) restent entiers, mais la passe à blanc les liste en `GUARDED` et la revue les présente en dernier, en les gardant sauf demande de découpe. L'artiste n'est réécrit que sur les pistes qui ont un artiste d'album. `import` le lance sur ce qu'il range et `enrich` sur toute la bibliothèque ; détails dans [docs/splitting.md](docs/splitting.md) (en anglais).
- **`dedupe`** repère les noms d'un même artiste écrits de plusieurs façons : identiques une fois la casse, les accents, la ponctuation et un `The` initial mis de côté (`Beyonce` / `Beyoncé`), même identifiant MusicBrainz, orthographe proche (`Mettalica`), ou nom partiel (`Bach` / `Johann Sebastian Bach`). `dedupe --review` présente chaque groupe un par un et garde les réponses dans `state/artist-aliases.txt` (`Beyonce => Beyoncé`, ou `John Lennon != Julian Lennon`), et `--apply` réécrit les variantes dans les tags. `import` et `enrich` appliquent ensuite les alias d'eux-mêmes ; détails dans [docs/dedupe.md](docs/dedupe.md) (en anglais).
- **`watch`** est le processus permanent du conteneur : il importe ce qui arrive dans l'Inbox une fois la copie terminée (et lance l'import une fois par jour, au démarrage puis à chaque nouveau jour, pour purger `_done/`), et peut lancer `organize --since` chaque nuit (`--organize-at`) et `enrich --rescan` chaque semaine (`--enrich-at "sun 03:00"`, jours en anglais ou en français) : le cache des métadonnées est ainsi relu en entier une fois par semaine.
- **`tag CHEMIN… --set CHAMP=VALEUR`** réécrit des champs de tags de pistes ou de toutes les pistes d'un dossier (titre, artiste, artiste de l'album, album, numéros et totaux de piste et de disque, année, genre, compositeur, flag compilation ; une valeur vide efface le champ), puis les range là où leurs nouveaux tags les placent, changements de tags et déplacements dans un même journal. Le dry run montre les deux ; un genre prend l'orthographe de la liste validée.
- **`undo JOURNAL`** rejoue le journal d'un passage à l'envers, déplacements comme changements de tags.
- **Le dashboard**, servi par `watch --listen` (sur le port 8080 avec le fichier compose), montre les passages en cours et les derniers avec leur sortie suivie en direct, la programmation, l'Inbox et les questions de revue en attente de réponse, en français ou en anglais. Il lance aussi n'importe quelle commande, d'abord en dry run, puis l'applique ou annule un passage appliqué depuis sa page, arrête un passage en cours ou retire une demande encore en file, parcourt la bibliothèque pour éditer les tags d'un album dans un formulaire, passé à `tag`, ou rechercher à nouveau ses paroles, et pose les questions de `split --review` et `dedupe --review`, et sur les genres que la liste validée ne connaît pas, une page à la fois ; il n'applique que si `watch` tourne lui-même avec `--apply` ; détails dans [docs/web.md](docs/web.md) (en anglais).
- En option, pour une bibliothèque servie par [Lyrion Music Server](https://lyrion.org) (ex-Logitech Media Server), les lectures et notes suivent les fichiers déplacés au lieu de rester orphelines : les déplacements sont reportés dans son `persist.db`. Le plugin Alternative Play Count doit être installé, sa table étant mise à jour aussi (voir [docs/lyrion.md](docs/lyrion.md), en anglais).

## Lancement

Docker est la voie prévue. ffmpeg est dans l'image, et les tests tournent au build : une image cassée n'est jamais produite.

```bash
cp .env.example .env      # chemins de la bibliothèque, de l'Inbox et (facultatif) de Lyrion
mkdir state               # journaux, dernier passage, verrou, cache des métadonnées et historique des passages ; à créer avant le premier lancement
docker compose build
docker compose up -d      # watch --apply, rangement à 02:00, enrichissement le dimanche à 03:00, dashboard sur :8080
journalctl -t music-librarian -f
```

Chaque passage garde aussi sa sortie dans le dossier d'état, d'où qu'il soit lancé (`watch`, `docker compose run` ou un shell) : `state/runs/<id>.log`, les 200 derniers passages étant listés dans `state/runs.db` (commande, début et fin, code de sortie, résumé).

Pour utiliser l'image publiée plutôt que de la construire, remplacer la section `build:` et la ligne `image:` de `docker-compose.yml` par `image: ghcr.io/werdeil/music-librarian:0.1` (ou `latest`), puis `docker compose pull`. Le tag `dev` suit `main` entre deux versions, sans autre test que la CI. Les images sont construites pour amd64 et arm64.

La bibliothèque et l'Inbox doivent se trouver sous le dossier unique de `MUSIC_LIBRARIAN_VOLUME`, monté au même chemin que sur l'hôte : les déplacements restent des renommages et les chemins des journaux valent des deux côtés.

Lancements ponctuels, en dry-run sans `--apply` :

```bash
docker compose run --rm music-librarian organize --since
docker compose run --rm music-librarian import
docker compose run --rm music-librarian enrich
docker compose run --rm music-librarian scan        # ne modifie rien dans la bibliothèque
docker compose run --rm music-librarian tag "/srv/music/Library/Beyonce/4" --set albumartist=Beyoncé
docker compose run --rm music-librarian split
docker compose run --rm music-librarian split --review   # interactif, n'écrit que les décisions
docker compose run --rm music-librarian dedupe --review  # interactif, n'écrit que les alias
docker compose run --rm music-librarian undo /state/journal/{date}.jsonl --apply
```

Sans Docker : Python 3.12, ffmpeg dans le `PATH` et `pip install .` (`pip install -r requirements.txt .` pour les versions testées), puis `music-librarian {commande} --help` (`music-librarian --version` pour la version).

## Configuration

| Variable | Rôle |
|---|---|
| `MUSIC_LIBRARIAN_ROOT` | Racine de la bibliothèque (`--root`) |
| `MUSIC_LIBRARIAN_INBOX` | Dossier de dépôt, hors de la bibliothèque pour qu'un serveur de musique ne l'indexe pas (`--inbox`) |
| `MUSIC_LIBRARIAN_STATE` | Journaux, date du dernier passage, verrou, cache des métadonnées, historique des passages, décisions de découpage des crédits et alias d'artistes (`--state-dir` ; `/state` dans l'image, `~/.local/state/music-librarian` sinon) |
| `LYRION_PERSIST_DB`, `LYRION_MUSIC_ROOT` | `persist.db` de Lyrion et racine de la bibliothèque vue par Lyrion ; sans eux, l'historique n'est pas reporté |
| `LYRICS_PROVIDERS` | Fournisseurs de paroles essayés dans l'ordre (défaut `lrclib,netease`, voir ci-dessous ; vide pour ne chercher aucunes paroles) |
| `MUSIXMATCH_TOKEN`, `LRCLIB_TIMEOUT` | Réglages facultatifs des fournisseurs |
| `LYRICS_RETRY_DAYS` | Jours avant de rechercher à nouveau les paroles d'une piste restée sans résultat (défaut `30`, `0` pour chercher à chaque passage) |
| `MUSIC_LIBRARIAN_GENRES` | Liste des genres validés (défaut `genres.txt` dans le dossier d'état, voir ci-dessous) |
| `GENRE_LOOKUP`, `ITUNES_COUNTRY` | `itunes` pour chercher les genres que la liste ne sait pas aligner (activé dans le fichier compose), et le magasin interrogé (défaut `us`) |
| `MUSIC_LIBRARIAN_LISTEN` | Adresse du dashboard, `[HÔTE:]PORT` (`watch --listen` ; le fichier compose fixe `8080`, et `WEB_PORT` le port de l'hôte) |

Le fichier compose lit ses propres variables dans `.env` ; `.env.example` les documente.

### Fournisseurs de paroles

[LRCLIB](https://lrclib.net) est une base ouverte avec une API publique. NetEase Cloud Music et Musixmatch sont interrogés par les points d'accès de leurs propres applications, qui ne sont pas des API publiques : ils peuvent changer ou bloquer les requêtes à tout moment, et leur usage peut contrevenir à leurs conditions d'utilisation. Musixmatch est donc désactivé par défaut ; l'ajouter à `LYRICS_PROVIDERS` (par exemple `lrclib,musixmatch,netease`) si vous l'acceptez. NetEase peut être retiré de la même façon.

### Genres

Avec une liste de genres validés, `import` et `enrich` réécrivent le genre de chaque piste dans l'orthographe de la liste. Copier [`genres.example.txt`](genres.example.txt) en `state/genres.txt` et l'adapter : un genre validé par ligne, suivi au besoin de deux-points et des noms qui le désignent (`Rock Dur: hard rock, metal`). Les noms sont comparés sans tenir compte de la casse, des accents, des espaces ni de la ponctuation, et un tag qui porte plusieurs genres (`Rock; Pop`) prend le premier que la liste connaît. Un genre inconnu de la liste est laissé tel quel et signalé `UNKNOWN` : l'ajouter à la liste, à la main ou depuis la page Revues du dashboard, et le prochain `enrich` hebdomadaire l'aligne. Sans liste, les genres ne sont pas touchés.

Avec `GENRE_LOOKUP=itunes`, un album dont la liste ne sait pas aligner le genre, ou qui n'en a pas, est cherché sur l'[API de recherche iTunes](https://performance-partners.apple.com/search-api), gratuite et sans clé : quand la liste connaît le genre qu'Apple donne à cet album (l'exemple reprend les noms d'Apple), ses pistes le prennent, avec la ligne `ITUNES` dans le journal. Sinon le genre d'Apple est affiché à côté de `UNKNOWN`, pour pouvoir l'ajouter à la liste. Les réponses sont gardées dans `state/genre_lookup.json` ; un album qu'Apple n'a pas est recherché de nouveau après 30 jours. `ITUNES_COUNTRY` choisit le magasin (`fr` pour des noms de genres en français).

## Sécurité

Le dashboard n'a pas d'authentification, par choix : quiconque atteint son port voit les chemins de la bibliothèque, l'Inbox et la sortie de chaque passage, et peut lancer n'importe quelle commande sur la bibliothèque. Gardez-le sur le réseau de la maison : pas de redirection de port, pas de reverse proxy public. Pour y accéder de l'extérieur, rejoignez le réseau par un VPN comme WireGuard ou Tailscale.

## Documentation

En anglais :

- [Dashboard](docs/web.md) : ses pages, les états d'un passage, et ses routes
- [Règles de nommage](docs/naming-rules.md) : construction des chemins cibles, et quand un fichier est considéré en place
- [Découpage des crédits](docs/splitting.md) : quels compositeurs et artistes sont découpés, et le fichier de décisions
- [Unification des noms d'artistes](docs/dedupe.md) : quels noms sont regroupés, et le fichier d'alias
- [Report de l'historique Lyrion](docs/lyrion.md)
- [Architecture](docs/architecture.md) : organisation du paquet et déroulé de chaque commande, étape par étape

## Développement

```bash
pip install -r requirements-dev.txt -e .
python -m pytest -q    # demande ffmpeg
pylint src tests
```

Voir [CONTRIBUTING.md](CONTRIBUTING.md) avant d'ouvrir une pull request, et les [releases](https://github.com/werdeil/music-librarian/releases) pour les changements entre versions (en anglais).

## Licence

[MIT](LICENSE)
