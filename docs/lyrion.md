# Lyrion history

[Lyrion Music Server](https://lyrion.org) (formerly Logitech Media Server) keys play counts and ratings on each file's URL. When a file is renamed, Lyrion's next scan creates a fresh entry for the new path and leaves the old one orphaned: the history is lost.

With the carry-over on, every move made by `organize`, `import` or `undo` is reported into Lyrion's `persist.db`, so the history follows the file. The history left behind by a rename made outside a pass is given back by [`playcounts`](#orphan-play-counts).

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

## Orphan play counts

`playcounts` gives back the history a carry-over could not follow: files renamed by hand or by another tool, or before the carry-over was set up. It takes the same two settings.

```bash
music-librarian playcounts             # dry run: what would be written, the orphans awaiting an answer
music-librarian playcounts --review    # answer the orphans, in a terminal
music-librarian playcounts --apply     # write to persist.db
```

An **orphan** is an entry of `tracks_persistent` for a file, with plays or a rating, that has no entry in the plugin's `alternativeplaycount`, which drops those of the files gone, and no file in the library. Each one is settled in turn by:

- **a journal**: when the moves of the journals in the state folder lead the orphan's file to a track of the library, it is given to that track without a question (`JOURNAL`);
- **a match**: the track picked for it with `--review`, kept in `playcount-matches.json` in the state folder until it is applied (`MATCH`). A match whose track left the library since is `SKIPPED`, and the pass exits with code 2;
- **nothing yet**: it waits (`WAITING`, shown in a dry run with its best match), or was ignored in the review and is never asked about again.

`--review` shows the orphans one at a time, the most played first, each with the five tracks whose path looks most like its own: the note out of 100 weighs the title for 70, the artist folder for 20 and the album for 10, plus 5 when both have play counts within 20 % of each other. A number picks a track, `i` ignores the orphan, Enter leaves it for later, `u` takes the previous answer back. The dashboard's **Lyrion** page asks the same questions and queues the pass (see [web.md](web.md#lyrion)).

A settled orphan is merged into its track as a carry-over does: in `tracks_persistent` its entry takes the track's URL, or is merged into the track's entry and deleted; in `alternativeplaycount` its plays are added to the track's and the latest play kept, and the track's `dynPSval` is computed again from its counts.

The pass also does three repairs:

- **Rekey** (`REKEY`, `--no-rekey` to skip it): an entry of either table whose `urlmd5` is not the MD5 of its URL is given the right one, or merged into the entry that already has it. Lyrion and the plugin look entries up by `urlmd5`, so such an entry is lost to them although its URL is right.
- **Backfill** (`BACKFILL`, `--no-backfill` to skip it): a track Lyrion counts no play for while the plugin counts some gets the plugin's play count and last play.
- **DPSV** (`DPSV`, only with `--dpsv`): the plugin's `dynPSval` of every track is computed again from its plays and skips. The table does not keep the order of the events, so the value is exact for a track never skipped only: for the others it replaces the plugin's own value by an estimate.

The safety is the carry-over's: one short transaction after a backup copy, taken only when there is something to write. These writes are not journaled, so `undo` does not take them back: the backup copy does.
