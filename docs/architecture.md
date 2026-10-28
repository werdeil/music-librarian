# Architecture

music-librarian is a single Python package, `music_librarian`, with one command-line entry point. This page is a map for contributors.

## Layout

```
src/music_librarian/
├── cli.py           music-librarian <command>: dispatches to the modules below
├── importer.py      import: Inbox items → conversion → organize → enrich
├── watch.py         watch: the long-running loop of the container
├── scan.py          scan: the whole library's metadata read into the cache
├── core/            what every command shares
│   ├── files.py     audio extensions, junk files, emptied folders
│   ├── journal.py   one JSON line per move, so a pass can be undone
│   ├── lock.py      one --apply pass at a time, clean stop on SIGTERM/Ctrl-C, exit codes
│   ├── log.py       stdout, plus journald with --syslog
│   └── state.py     where journals, the last pass date, the lock and the metadata cache live
├── organize/        organize and undo
│   ├── naming.py    target paths, see naming-rules.md
│   ├── scan.py      which files a pass reads, and their tags
│   ├── organize.py  the filing pass
│   └── undo.py      replaying a journal backwards
├── enrich/          enrich: genres, ReplayGain, covers, lyrics on the whole library
│   ├── common.py    album folders under the targets, summary and exit code
│   ├── covers.py    folder.jpg embedded into the tracks
│   ├── genres.py    genres aligned with the validated list
│   └── lyrics.py    lyrics written into the tags
├── lyrics/          lyrics lookup: provider chain, matching
│   ├── lrclib.py, musixmatch.py, netease.py   one module per provider
│   └── matching.py  is a provider's answer this recording?
├── media/           tags.py (genres, lyrics and covers in the tags), metadata_cache.py (each file's
│                    tags kept between passes), artwork.py (image sizes), convert.py (FLAC → ALAC
│                    and its checks)
└── integrations/    lyrion.py, see lyrion.md
```

## How a pass runs

- **organize** walks the library (or only the folders changed since the last full pass with `--since`), computes each file's target, holds back conflicts, then moves files one by one. Each move is written to the journal right after it happens, so an interrupted pass can still be undone.
- **import** never files from the Inbox directly. It builds a work folder in `_processing/` with the FLAC files converted to ALAC and the others hard-linked (copied across disks), checks that `organize` would file all of it, then files it in one pass. On success the original goes to `_done/`; on any problem the whole item goes to `_failed/`.
- **enrich** hands album folders in batches to the `enrich.genres`, `enrich.replaygain`, `enrich.covers` and `enrich.lyrics` modules, run as subprocesses. Each tool only touches what is missing. A complete pass then drops the files gone from the library from the metadata cache.
- **The metadata cache** (`media/metadata_cache.py`, `metadata.db` in the state folder) holds one snapshot per music file: its text tags, duration, whether it has lyrics and ReplayGain, and the hash of its embedded cover. A snapshot is read again only when the file's size, mtime or ctime changed, so after one full read (`scan`, or any first pass) the enrichment tools only open the files that changed. The tools run as separate processes, possibly at the same time as a manual run: SQLite serializes their writes. The cache is state, not the library, so dry runs fill it too. `scan --force` and `enrich --rescan` read every file again; the weekly `enrich` of `watch` runs with `--rescan`, so a stale snapshot never outlives a week.
- **watch** reads the Inbox root every `--interval` seconds and runs `import` once a drop has stopped changing. It also runs `organize --since` every night and `enrich` every week when asked to. Each of the three is a job with its own state (`Imports`, `NightlyOrganize`, `WeeklyEnrich`) that the loop ticks in turn.

## Conventions

- Everything is a dry run without `--apply`.
- `organize`, `import` and `enrich` each parse their arguments in `main(argv)` into an `Options` dataclass, then do the work in `run(opts, log)`. Commands call each other through these functions, never by building an argument list: `watch` builds the options of each pass once, at startup.
- Only one `--apply` pass runs at a time, through a lock in the state folder that `run` takes. The lock is not reentrant: `import` files through `organize_pass`, the unlocked function under `organize.run`, while it holds the lock itself.
- A stop request (SIGTERM from `docker stop`, or Ctrl-C) lets the current file operation finish, then stops. A second Ctrl-C stops at once.
- Exit codes: 0 OK, 2 something was skipped or failed, 3 another pass holds the lock, 4 stopped before the end, 5 the Lyrion carry-over failed.
- Imports inside the package are absolute: `from music_librarian.core.lock import STOP`.
- The tests mirror the package under `tests/`.
