# Lyrion history carry-over

[Lyrion Music Server](https://lyrion.org) (formerly Logitech Media Server) keys play counts and ratings on each file's URL. When a file is renamed, Lyrion's next scan creates a fresh entry for the new path and leaves the old one orphaned: the history is lost.

With the carry-over on, every move made by `organize`, `import` or `undo` is reported into Lyrion's `persist.db`, so the history follows the file.

## Setting it up

Two settings, both required:

| Variable | Option | Meaning |
|---|---|---|
| `LYRION_PERSIST_DB` | `--lyrion-db` | Path to Lyrion's `persist.db` |
| `LYRION_MUSIC_ROOT` | `--lyrion-root` | The library root as Lyrion sees it (its own mount point, e.g. `/music`) |

Without `LYRION_PERSIST_DB`, the carry-over is off.

With Docker Compose, set these in `.env`:

```bash
LYRION_PREFS_DIR=/srv/lyrion/prefs   # the folder holding persist.db
LYRION_MUSIC_ROOT=/music
```

The whole prefs folder is mounted, not just the file: SQLite also writes `persist.db-wal` and `persist.db-shm` next to it.

The Alternative Play Count plugin must be installed. Its `alternativeplaycount` table is updated along with Lyrion's own `tracks_persistent`, and the carry-over fails if that table is missing.

## What happens to an entry

Entries are looked up by `urlmd5`, the MD5 of the file URL. For each moved audio file, in both tables:

- **No entry for the old path**: nothing to do (`no history` in the summary).
- **No entry for the new path yet**, the usual case: the old entry takes the new URL (`renamed`).
- **Both exist**, because Lyrion already scanned the new path: the old entry is merged into the new one, then deleted (`merged`). Counts (`playCount`, `skipCount`) are summed, dates (`lastPlayed`, `lastRated`, `lastSkipped`) keep the latest, `rating`, `prevRating` and `musicbrainz_id` keep the new entry's value when it has one, `added` keeps the earliest, and the plugin's `dynPSval` comes from the entry with the most plays and skips.

URLs are built the way Lyrion writes them: `file://` + the Lyrion root + the path relative to the library, percent-encoded except for `-_.!~*()/:@&=+$,[]`.

## Safety

- Lyrion can keep running. All updates of a pass happen in one short `BEGIN IMMEDIATE` transaction. A busy database is waited for up to 60 seconds.
- Before writing, the database is copied to `$MUSIC_LIBRARIAN_STATE/lyrion-backups/persist-YYYYMMDD-HHMMSS.db`, WAL included. The 14 most recent copies are kept.
- In a dry run, the database is opened read-only and the summary shows what would happen.

## When it fails

A failed carry-over never undoes the moves. The pass exits with code 5 (2 if it also had conflicts or skipped files), and the summary prints the command that retries it from the journal:

```bash
music-librarian organize --lyrion-replay /state/journal/20261019-020000.jsonl --apply
```
