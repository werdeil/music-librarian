# Architecture

music-librarian is a single Python package, `music_librarian`, with one command-line entry point. This page is a map for contributors.

## Layout

```
src/music_librarian/
├── cli.py           music-librarian <command>: dispatches to the modules below
├── importer.py      import: Inbox items → conversion → organize → enrich
├── watch.py         watch: the long-running loop of the container
├── core/            what every command shares
│   ├── files.py     audio extensions, junk files, emptied folders
│   ├── journal.py   one JSON line per move, so a pass can be undone
│   ├── lock.py      one --apply pass at a time, clean stop on SIGTERM/Ctrl-C, exit codes
│   ├── log.py       stdout, plus journald with --syslog
│   └── state.py     where journals, the last pass date and the lock live
├── organize/        organize and undo
│   ├── naming.py    target paths, see naming-rules.md
│   ├── scan.py      which files a pass reads, and their tags
│   ├── organize.py  the filing pass
│   └── undo.py      replaying a journal backwards
├── enrich/          enrich: ReplayGain, covers, lyrics on the whole library
│   ├── covers.py    folder.jpg embedded into the tracks
│   └── lyrics.py    lyrics written into the tags
├── lyrics/          lyrics lookup: provider chain, cache, matching
│   ├── lrclib.py, musixmatch.py, netease.py   one module per provider
│   ├── matching.py  is a provider's answer this recording?
│   └── cache.py     bounded in-memory cache
├── media/           tags.py (lyrics and covers in the tags), artwork.py (image sizes)
└── integrations/    lyrion.py, see lyrion.md
```

## How a pass runs

- **organize** walks the library (or only the folders changed since the last full pass with `--since`), computes each file's target, holds back conflicts, then moves files one by one. Each move is written to the journal right after it happens, so an interrupted pass can still be undone.
- **import** never files from the Inbox directly. It builds a work folder in `_processing/` with the FLAC files converted to ALAC and the others hard-linked (copied across disks), checks that `organize` would file all of it, then files it in one pass. On success the original goes to `_done/`; on any problem the whole item goes to `_failed/`.
- **enrich** hands album folders in batches to the `enrich.replaygain`, `enrich.covers` and `enrich.lyrics` modules, run as subprocesses. Each tool only touches what is missing.
- **watch** reads the Inbox root every `--interval` seconds and runs `import` once a drop has stopped changing. It also runs `organize --since` every night and `enrich` every week when asked to.

## Conventions

- Everything is a dry run without `--apply`.
- Only one `--apply` pass runs at a time, through a lock in the state folder. Within a process the lock is reentrant: `import` calls `organize` without blocking itself.
- A stop request (SIGTERM from `docker stop`, or Ctrl-C) lets the current file operation finish, then stops. A second Ctrl-C stops at once.
- Exit codes: 0 OK, 2 something was skipped or failed, 3 another pass holds the lock, 4 stopped before the end, 5 the Lyrion carry-over failed.
- Imports inside the package are absolute: `from music_librarian.core.lock import STOP`.
- The tests mirror the package under `tests/`.
