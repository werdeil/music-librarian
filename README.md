# music-librarian

**English** · [Français](README.fr.md)

Keeps a music library filed the way Apple Music does, and imports new music into it from a drop folder. Made for ALAC `.m4a` libraries with some MP3, it runs as a single container that watches the drop folder, files what lands there, and enriches the tags with ReplayGain, cover art and lyrics. It can also split the composer and artist credits that hold several names into the `;`-separated form Lyrion understands, and unify the names of one artist spelled several ways.

Everything is a dry run unless `--apply` is passed, and every move is journaled so a run can be undone.

## What it does

- **`organize`** files the library as `Album Artist/Album/NN Title.ext` (falling back to the artist), `Compilations/Album/…` when the compilation flag is set, and `D-NN Title` for multi-disc albums. Characters Apple Music replaces (`/ : ? " | ’ ´ * < > \` …) become `_`. Accent normalization (NFC/NFD), case-only differences and truncated names are tolerated so existing files are not renamed for nothing, unless a folder spelled exactly as the target already exists next to the variant: the variant's files then join it. Side files (`folder.jpg`, booklets, `.lrc`) follow their album; `.DS_Store` and `._*` are deleted. An album whose tracks disagree on the compilation flag is reported as a conflict and left alone. `.m4a` files whose index (`moov` atom) comes after the audio are rewritten with the index first, without re-encoding, so Lyrion streams them as `alc` instead of transcoding them as `alcx`. `--since` only rescans folders changed since the last full pass. The full rules are in [docs/naming-rules.md](docs/naming-rules.md).
- **`import`** takes album folders or loose files dropped in the Inbox (FLAC, ALAC, MP3), converts FLAC to ALAC with ffmpeg (tags and artwork kept, duration checked with ffprobe), files them all-or-nothing, and enriches only the target folders. Originals go to `_done/YYYY-MM-DD/` and are purged after 7 days; items that cannot be filed go to `_failed/`.
- **`enrich`** runs over the whole library and only fills what is missing or off: genres aligned with a validated list (see below), ReplayGain measured with ffmpeg, the embedded cover when `folder.jpg` differs from it, and lyrics from LRCLIB and NetEase, plus Musixmatch if enabled. Lyrics are only written when the provider's title, artist and duration match the file. A track looked up in vain is not looked up again for 30 days (`LYRICS_RETRY_DAYS`), unless its artist, title or album changes. A dry run lists the tracks whose lyrics it would look up, without looking them up. `--no-lyrics` leaves the lyrics out, by far the slowest part, and `--no-genres` the genres; such a pass does not stand in for the weekly one.
- **`scan`** reads the metadata of the whole library into a cache in the state folder (`metadata.db`), then prints the track count per genre. `organize`, `enrich`, `import`, `split` and `dedupe` read the tags through that cache, so after one full read (about 20 minutes for 25,000 tracks on a hard disk) a pass only opens the files whose size or dates changed, and a dry run followed by `--apply` reads the library once. Files gone from the library are dropped from the cache by `scan` and by every complete `enrich` pass. `scan --force` reads every file again whatever the cache holds, as `enrich --rescan` does before enriching.
- **`split`** rewrites composer and artist tags such as `John Lennon/Paul McCartney` as `John Lennon; Paul McCartney`, the separator Lyrion splits on. A comma or slash is only split when the library vouches for it: a credit that is an album artist somewhere (`AC/DC`, `Earth, Wind & Fire`) is kept, a credit whose fragments are all names seen on their own is split, and any other one waits for a decision in `state/split-decisions.txt` (`split`, `keep`, or the names to write). `split --review` asks about those one at a time with a suggestion that Enter accepts, can stop and resume at any point, and skips the credits an earlier answer already settles. Album artists that look like a collaboration of known names (`Iggy Pop, Muse`) stay whole, but the dry run lists them as `GUARDED` and the review asks about them last, keeping them unless told to split. The artist is only rewritten on tracks with an album artist. `import` runs it on what it files and `enrich` on the whole library; details in [docs/splitting.md](docs/splitting.md).
- **`dedupe`** finds the names of one artist spelled several ways: the same once case, accents, punctuation and a leading `The` are set aside (`Beyonce` / `Beyoncé`), the same MusicBrainz id, a close spelling (`Mettalica`), or a partial name (`Bach` / `Johann Sebastian Bach`). `dedupe --review` asks about each group one at a time and keeps the answers in `state/artist-aliases.txt` (`Beyonce => Beyoncé`, or `John Lennon != Julian Lennon`), and `--apply` rewrites the variants in the tags. `import` and `enrich` then apply the aliases by themselves; details in [docs/dedupe.md](docs/dedupe.md).
- **`watch`** is the container's long-running process: it imports whatever lands in the Inbox once the copy has settled (and runs the import once a day, at startup then each new day, to purge `_done/`), and can run `organize --since` nightly (`--organize-at`) and `enrich --rescan` weekly (`--enrich-at "sun 03:00"`), so the metadata cache is read again in full once a week.
- **`tag PATH… --set FIELD=VALUE`** rewrites tag fields of tracks or of every track of a folder (title, artist, album artist, album, track and disc numbers and totals, year, genre, composer, compilation flag; an empty value removes the field), then files them where their new tags put them, the tag changes and the moves in one journal. The dry run lists both; a genre takes the validated list's spelling.
- **`undo JOURNAL`** replays a run's journal in reverse, moves and tag changes alike.
- **The dashboard**, served by `watch --listen` (on port 8080 with the compose file), shows the passes running and the recent ones with their output followed live, the schedule, the Inbox and the review questions awaiting an answer, in English or French. It also runs any command, as a dry run first, then applies it or undoes an applied pass from its page, stops a pass running or cancels one still queued, browses the library to edit an album's tags in a form, run as `tag`, or look up its lyrics again, and asks the questions of `split --review` and `dedupe --review`, and about the genres the validated list does not know, one page at a time; it applies only when `watch` itself runs with `--apply`; details in [docs/web.md](docs/web.md).
- Optionally, for a [Lyrion Music Server](https://lyrion.org) (formerly Logitech Media Server) library, play counts and ratings follow moved files instead of being orphaned: the moves are carried over into its `persist.db`. The Alternative Play Count plugin must be installed, since its table is updated too (see [docs/lyrion.md](docs/lyrion.md)).

## Running it

Docker is the intended way. ffmpeg comes with the image, and the test suite runs during the build, so a broken image is never produced.

```bash
cp .env.example .env      # set the library, Inbox and (optionally) Lyrion paths
mkdir state               # journals, last run, lock, metadata cache and run history; must exist before the first start
docker compose build
docker compose up -d      # watch --apply, organize nightly at 02:00, enrich on Sundays at 03:00, dashboard on :8080
journalctl -t music-librarian -f
```

Each pass also keeps its output in the state folder, wherever it was started from (`watch`, `docker compose run` or a shell): `state/runs/<id>.log`, with the 200 latest passes listed in `state/runs.db` (command, start and end, exit code, summary).

To use the published image instead of building it, replace the `build:` section and the `image:` line of `docker-compose.yml` with `image: ghcr.io/werdeil/music-librarian:0.1` (or `latest`), then `docker compose pull`. The `dev` tag follows `main` between releases, untested beyond the CI. Images are built for amd64 and arm64.

The library and the Inbox must sit under the single folder set in `MUSIC_LIBRARIAN_VOLUME`, mounted at the same path as on the host, so moves stay renames and journal paths are valid on both sides.

One-off runs, dry-run unless `--apply` is added:

```bash
docker compose run --rm music-librarian organize --since
docker compose run --rm music-librarian import
docker compose run --rm music-librarian enrich
docker compose run --rm music-librarian scan        # changes nothing in the library
docker compose run --rm music-librarian tag "/srv/music/Library/Beyonce/4" --set albumartist=Beyoncé
docker compose run --rm music-librarian split
docker compose run --rm music-librarian split --review   # interactive, writes the decisions only
docker compose run --rm music-librarian dedupe --review  # interactive, writes the aliases only
docker compose run --rm music-librarian undo /state/journal/{date}.jsonl --apply
```

Without Docker: Python 3.12, ffmpeg on `PATH` and `pip install .` (`pip install -r requirements.txt .` for the tested versions), then `music-librarian {command} --help` (`music-librarian --version` for the version).

## Configuration

| Variable | Purpose |
|---|---|
| `MUSIC_LIBRARIAN_ROOT` | Library root (`--root`) |
| `MUSIC_LIBRARIAN_INBOX` | Drop folder, outside the library so a music server does not index it (`--inbox`) |
| `MUSIC_LIBRARIAN_STATE` | Journals, last run date, lock, metadata cache, run history, the credit split decisions and the artist aliases (`--state-dir`; `/state` in the image, `~/.local/state/music-librarian` otherwise) |
| `LYRION_PERSIST_DB`, `LYRION_MUSIC_ROOT` | Lyrion's `persist.db` and the library root as Lyrion sees it; history carry-over is off without them |
| `LYRICS_PROVIDERS` | Lyrics providers tried in order (default `lrclib,netease`, see below; empty to look up no lyrics) |
| `MUSIXMATCH_TOKEN`, `LRCLIB_TIMEOUT` | Optional provider settings |
| `LYRICS_RETRY_DAYS` | Days before a track whose lyrics were not found is looked up again (default `30`, `0` to look it up on every pass) |
| `MUSIC_LIBRARIAN_GENRES` | Validated genre list (default `genres.txt` in the state folder, see below) |
| `GENRE_LOOKUP`, `ITUNES_COUNTRY` | `itunes` to look up the genres the list cannot align (on in the compose file), and the store asked (default `us`) |
| `MUSIC_LIBRARIAN_LISTEN` | Address of the dashboard, `[HOST:]PORT` (`watch --listen`; the compose file sets `8080`, and `WEB_PORT` the host's port) |

The compose file reads its own variables from `.env`; `.env.example` documents them.

### Lyrics providers

[LRCLIB](https://lrclib.net) is an open database with a public API. NetEase Cloud Music and Musixmatch are reached through the endpoints of their own apps, which are not public APIs: they can change or block requests at any time, and using them may go against their terms of service. Musixmatch is therefore off by default; add it to `LYRICS_PROVIDERS` (e.g. `lrclib,musixmatch,netease`) if you accept that. Leave NetEase out the same way if you prefer.

### Genres

With a validated genre list, `import` and `enrich` rewrite each track's genre to the list's spelling. Copy [`genres.example.txt`](genres.example.txt) to `state/genres.txt` and adapt it: one validated genre per line, optionally followed by a colon and the names that stand for it (`Rock Dur: hard rock, metal`). Names are compared without case, accents, spaces or punctuation, and a tag holding several genres (`Rock; Pop`) takes the first one the list knows. A genre the list does not know is left as is and reported as `UNKNOWN`: add it to the list, by hand or from the dashboard's Reviews page, and the next weekly `enrich` aligns it. Without a list, genres are left alone.

With `GENRE_LOOKUP=itunes`, an album whose genre the list cannot align, or that has none, is searched on the [iTunes Search API](https://performance-partners.apple.com/search-api), free and without a key: when the list knows Apple's genre for that album (the example maps Apple's names), its tracks take it, logged as `ITUNES`. Otherwise Apple's genre is shown next to `UNKNOWN`, so it can be added to the list. Answers are kept in `state/genre_lookup.json`; an album Apple does not have is searched again after 30 days. Choose the store with `ITUNES_COUNTRY` (`fr` for French genre names).

## Security

The dashboard has no authentication, by design: anyone who can reach its port sees the library's paths, the Inbox and the output of every pass, and can run any command on the library. Keep it on the home network: no port forwarding, no public reverse proxy. For remote access, join the network through a VPN such as WireGuard or Tailscale.

## Documentation

- [Dashboard](docs/web.md): its pages, the statuses of a run, and its routes
- [Naming rules](docs/naming-rules.md): how target paths are built, and when a file counts as in place
- [Splitting credits](docs/splitting.md): which composer and artist credits are split, and the decisions file
- [Unifying artist names](docs/dedupe.md): which names are grouped, and the aliases file
- [Lyrion history carry-over](docs/lyrion.md)
- [Architecture](docs/architecture.md): the package layout and how each command runs, step by step

## Development

```bash
pip install -r requirements-dev.txt -e .
python -m pytest -q    # needs ffmpeg
pylint src tests
```

See [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request, and the [releases](https://github.com/werdeil/music-librarian/releases) for what changed between versions.

## License

[MIT](LICENSE)
