# music-librarian

**English** · [Français](README.fr.md)

Keeps a music library filed the way Apple Music does, and imports new music into it from a drop folder. Made for ALAC `.m4a` libraries with some MP3, it runs as a single container that watches the drop folder, files what lands there, and enriches the tags with ReplayGain, cover art and lyrics.

Everything is a dry run unless `--apply` is passed, and every move is journaled so a run can be undone.

## What it does

- **`range`** files the library as `Album Artist/Album/NN Title.ext` (falling back to the artist), `Compilations/Album/…` when the compilation flag is set, and `D-NN Title` for multi-disc albums. Characters Apple Music replaces (`/ : ? " | ’ ´ * < > \` …) become `_`. Accent normalization (NFC/NFD), case-only differences and truncated names are tolerated so existing files are not renamed for nothing. Side files (`folder.jpg`, booklets, `.lrc`) follow their album; `.DS_Store` and `._*` are deleted. An album whose tracks disagree on the compilation flag is reported as a conflict and left alone. `--since` only rescans folders changed since the last full pass.
- **`import`** takes album folders or loose files dropped in the Inbox (FLAC, ALAC, MP3), converts FLAC to ALAC with ffmpeg (tags and artwork kept, duration checked with ffprobe), files them all-or-nothing, and enriches only the target folders. Originals go to `_traités/YYYY-MM-DD/` and are purged after 7 days; items that cannot be filed go to `_erreurs/`.
- **`enrich`** runs over the whole library and only fills what is missing: ReplayGain (r128gain), the embedded cover when `folder.jpg` differs from it, and lyrics from LRCLIB, Musixmatch and NetEase. Lyrics are only written when the provider's title, artist and duration match the file.
- **`watch`** is the container's long-running process: it imports whatever lands in the Inbox once the copy has settled, and can run `range --since` nightly and `enrich` weekly.
- **`undo JOURNAL`** replays a run's journal in reverse.
- Optionally, for a [Lyrion Music Server](https://lyrion.org) (formerly Logitech Media Server) library, play counts and ratings follow moved files instead of being orphaned: the moves are carried over into its `persist.db`. The Alternative Play Count plugin must be installed, since its table is updated too (see `lyrion.py`).

## Running it

Docker is the intended way. ffmpeg and r128gain come with the image, and the test suite runs during the build, so a broken image is never produced.

```bash
cp .env.example .env      # set the library, Inbox and (optionally) Lyrion paths
mkdir state               # journals, last run and lock; must exist before the first start
docker compose build
docker compose up -d      # watch --apply, range nightly at 02:00, enrich on Sundays at 03:00
journalctl -t music-librarian -f
```

The library and the Inbox must sit under the single folder set in `MUSIC_LIBRARIAN_VOLUME`, mounted at the same path as on the host, so moves stay renames and journal paths are valid on both sides.

One-off runs, dry-run unless `--apply` is added:

```bash
docker compose run --rm music-librarian range --since
docker compose run --rm music-librarian import
docker compose run --rm music-librarian enrich
docker compose run --rm music-librarian undo /state/journal/{date}.jsonl --apply
```

Without Docker: Python 3.12, ffmpeg, `pip install -r requirements.txt` and r128gain 1.0.7 on `PATH` (or in `R128GAIN`), then `./music-librarian {command} --help`.

## Configuration

| Variable | Purpose |
|---|---|
| `MUSIC_LIBRARIAN_ROOT` | Library root (`--root`) |
| `MUSIC_LIBRARIAN_INBOX` | Drop folder, outside the library so a music server does not index it (`--inbox`) |
| `MUSIC_LIBRARIAN_STATE` | Journals, last run date and lock (`--state-dir`; `/state` in the image) |
| `LYRION_PERSIST_DB`, `LYRION_MUSIC_ROOT` | Lyrion's `persist.db` and the library root as Lyrion sees it; history carry-over is off without them |
| `LYRICS_PROVIDERS` | Lyrics providers tried in order (default `lrclib,musixmatch,netease`) |
| `MUSIXMATCH_TOKEN`, `LRCLIB_TIMEOUT` | Optional provider settings |
| `R128GAIN` | r128gain executable |

The compose file reads its own variables from `.env`; `.env.example` documents them.

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest -q tests     # needs ffmpeg
pylint *.py music-librarian tests
```

The CLI output is in French.

## License

[MIT](LICENSE)
