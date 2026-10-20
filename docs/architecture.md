# Architecture

music-librarian is a single Python package, `music_librarian`, with one command-line entry point. This page is a map for contributors.

## Layout

```
src/music_librarian/
├── cli.py           music-librarian <command>: dispatches to the modules below
├── importer.py      import: Inbox items → conversion → organize → enrich → split
├── watch.py         watch: the long-running loop of the container
├── core/            what every command shares
│   ├── files.py     audio extensions, junk files, emptied folders
│   ├── journal.py   one JSON line per move or tag change, so a pass can be undone
│   ├── lock.py      one --apply pass at a time, clean stop on SIGTERM/Ctrl-C, exit codes
│   ├── log.py       stdout, plus journald with --syslog
│   └── state.py     where journals, the last pass date and the lock live
├── organize/        organize and undo
│   ├── naming.py    target paths, see naming-rules.md
│   ├── scan.py      which files a pass reads, and their tags
│   ├── organize.py  the filing pass
│   └── undo.py      replaying a journal backwards
├── split/           split: credits holding several names, see splitting.md
│   ├── rules.py     which credits are split, kept or left for a decision
│   ├── review.py    --review: one credit at a time
│   └── decisions.py the decisions file
├── enrich/          enrich: ReplayGain, covers, lyrics on the whole library
│   ├── common.py    album folders under the targets, summary and exit code
│   ├── covers.py    folder.jpg embedded into the tracks
│   └── lyrics.py    lyrics written into the tags
├── lyrics/          lyrics lookup: provider chain, matching
│   ├── lrclib.py, musixmatch.py, netease.py   one module per provider
│   └── matching.py  is a provider's answer this recording?
├── media/           tags.py (lyrics and covers in the tags), credit_tags.py (album artist, artist,
│                    composer), artwork.py (image sizes), convert.py (FLAC → ALAC and its checks)
└── integrations/    lyrion.py, see lyrion.md
```

## How a pass runs

- **organize** walks the library (or only the folders changed since the last full pass with `--since`), computes each file's target, holds back conflicts, then moves files one by one. Each move is written to the journal right after it happens, so an interrupted pass can still be undone.
- **import** never files from the Inbox directly. It builds a work folder in `_processing/` with the FLAC files converted to ALAC and the others hard-linked (copied across disks), checks that `organize` would file all of it, then files it in one pass. On success the original goes to `_done/`; on any problem the whole item goes to `_failed/`.
- **enrich** hands album folders in batches to the `enrich.replaygain`, `enrich.covers` and `enrich.lyrics` modules, run as subprocesses. Each tool only touches what is missing. It then runs `split` over the whole library.
- **split** reads the credits of the whole library (or of the folders given), keeps the names it knows, rewrites the credits it can settle and journals each tag change. A partial pass, such as the one `import` runs on its target folders, reads the known names a full pass left in the state folder.
- **watch** reads the Inbox root every `--interval` seconds and runs `import` once a drop has stopped changing. It also runs `organize --since` every night and `enrich` every week when asked to. Each of the three is a job with its own state (`Imports`, `NightlyOrganize`, `WeeklyEnrich`) that the loop ticks in turn.

## Conventions

- Everything is a dry run without `--apply`.
- `organize`, `import`, `enrich` and `split` each parse their arguments in `main(argv)` into an `Options` dataclass, then do the work in `run(opts, log)`. Commands call each other through these functions, never by building an argument list: `watch` builds the options of each pass once, at startup.
- Only one `--apply` pass runs at a time, through a lock in the state folder that `run` takes. The lock is not reentrant: `import` files through `organize_pass`, the unlocked function under `organize.run`, while it holds the lock itself.
- A stop request (SIGTERM from `docker stop`, or Ctrl-C) lets the current file operation finish, then stops. A second Ctrl-C stops at once.
- Exit codes: 0 OK, 2 something was skipped or failed, 3 another pass holds the lock, 4 stopped before the end, 5 the Lyrion carry-over failed.
- Imports inside the package are absolute: `from music_librarian.core.lock import STOP`.
- The tests mirror the package under `tests/`.
