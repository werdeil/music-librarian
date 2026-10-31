# Architecture

music-librarian is a single Python package, `music_librarian`, with one command-line entry point. This page is a map for contributors.

## Layout

```
src/music_librarian/
├── cli.py           music-librarian <command>: dispatches to the modules below
├── importer.py      import: Inbox items → conversion → organize → enrich → split → dedupe
├── watch.py         watch: the long-running loop of the container, and the dashboard's server
├── scan.py          scan: the whole library's metadata read into the cache
├── tag.py           tag: tag fields rewritten, then the tracks filed again
├── core/            what every command shares
│   ├── arguments.py the options every command takes: --root, --apply, --state-dir, --syslog
│   ├── files.py     audio extensions, junk files, emptied folders
│   ├── journal.py   one JSON line per move or tag change, so a pass can be undone
│   ├── lock.py      one --apply pass at a time, clean stop on SIGTERM/Ctrl-C, exit codes
│   ├── log.py       stdout, plus journald with --syslog, plus the run history
│   ├── runs.py      the run history: each pass's output and summary
│   └── state.py     where journals, the last pass date, the lock and the metadata cache live
├── organize/        organize and undo
│   ├── naming.py    target paths, see naming-rules.md
│   ├── scan.py      which files a pass reads, and their tags
│   ├── organize.py  the filing pass
│   └── undo.py      replaying a journal backwards
├── split/           split: credits holding several names, see splitting.md
│   ├── rules.py     which credits are split, kept or left for a decision
│   ├── review.py    --review: one credit at a time
│   ├── library.py   reading (through the metadata cache) and rewriting credits, the command frame
│   └── decisions.py the decisions file
├── dedupe/          dedupe: one artist spelled several ways, see dedupe.md
│   ├── similar.py   which names are grouped
│   ├── aliases.py   the aliases file
│   └── review.py    --review: one group at a time
├── enrich/          enrich: genres, ReplayGain, covers, lyrics on the whole library
│   ├── common.py    album folders under the targets, summary and exit code
│   ├── covers.py    folder.jpg embedded into the tracks
│   ├── genres.py    genres aligned with the validated list
│   └── lyrics.py    lyrics written into the tags
├── lyrics/          lyrics lookup: provider chain, matching
│   ├── lrclib.py, musixmatch.py, netease.py   one module per provider
│   ├── matching.py  is a provider's answer this recording?
│   └── misses.py    lookups that found nothing, not repeated before LYRICS_RETRY_DAYS
├── media/           tags.py (genres, lyrics and covers in the tags), metadata_cache.py (each file's
│                    tags kept between passes), fields.py (every field tag and undo rewrite, in each
│                    format), credit_tags.py (album artist, artist, composer), artwork.py (image
│                    sizes), convert.py (FLAC → ALAC and its checks), atoms.py (where moov sits in
│                    an MP4 file), faststart.py (moov moved ahead of mdat in existing MP4 files)
├── integrations/    lyrion.py (see lyrion.md), itunes.py (album genres)
└── web/             the dashboard served by watch --listen, see web.md
    ├── app.py       the pages, read from the state folder and the Inbox on each request
    ├── server.py    waitress in a thread of watch
    ├── passes.py    the passes asked for, queued for the watch loop, and how each is built
    ├── library.py   the albums from the metadata cache, an album's fields and the edits of its form
    ├── reviews.py   the split, dedupe and genre reviews, one question per page
    ├── i18n.py      the strings shown, in French and English
    └── templates/, static/
```

## Who runs what

| Trigger | Command | What it does |
|---|---|---|
| a drop in the Inbox has settled | `import` | files and enriches that drop, then splits and unifies its credits |
| every night (`--organize-at`) | `organize --since` | refiles the folders changed since the last full pass |
| every week (`--enrich-at`) | `enrich --rescan` | reads the whole library again, then fills genres, gain, covers and lyrics, and applies the split decisions and artist aliases |
| by hand | `scan` | fills the metadata cache and prints the track count per genre |
| by hand, or the dashboard's Reviews page | `split --review`, `dedupe --review` | asks about the credits and names the library cannot settle |
| by hand, or an album's page of the dashboard | `tag PATH --set FIELD=VALUE` | rewrites tag fields of tracks, then files them where their new tags put them |
| by hand | `undo JOURNAL` | replays a pass backwards, moves and tag changes alike |
| `watch --listen` | the dashboard | shows the passes running and the recent ones, the schedule and the Inbox; queues the passes asked for, which `watch` runs between its jobs |

The first three are run by **watch**, which reads the Inbox root every `--interval` seconds (30 by default). Each of its three jobs keeps its own state (`Imports`, `NightlyOrganize`, `WeeklyEnrich`) and the loop ticks them in turn. With `--listen`, a fourth job comes first, `Requests`: the passes asked for from the dashboard, which its thread only queues; a request cuts the pause between two ticks short, so it starts within half a second unless a pass is running. A job that finds the lock taken (exit code 3) is not marked done and tries again on the next tick. The nightly and weekly jobs read their last pass date from the state folder, so a restart of the container does not run them twice.

## How a pass runs

### import: Inbox to library, all or nothing

1. **Wait for the copy to finish.** An item (an album folder or a loose file) must not have changed for `--settle` seconds (10 by default), otherwise it is reported `WAITING` and left for the next pass.
2. **Sort its files** into FLAC, ALAC or MP3, side files (jpg, pdf, lrc, cue) and junk. Any other audio format sends the item to `_failed/`.
3. **Build a work folder** in `_processing/`: FLAC converted to ALAC (`media/convert.py`: codec, duration within 0.1 s, tags and cover checked), the other files hard-linked (copied across disks), `cover.jpg` or `front.jpg` renamed `folder.jpg`. A failed check sends the item to `_failed/`.
4. **Ask organize, in a dry run, whether it would file all of it.** A single conflict or missing tag sends the whole item to `_failed/`.
5. **File it** through `organize_pass`, not interruptible: a stop request lets the whole item finish.
6. **Move the original** to `_done/YYYY-MM-DD/`, purged after `--keep-days` (7 by default).
7. **Enrich the target folders only** (genres, ReplayGain, cover, lyrics). A failed tool does not fail the import: the weekly pass catches it up.
8. **Split, then unify, their credits** with `split` and `dedupe`, applying the decisions and aliases already taken (`--no-split`, `--no-dedupe` to skip them).

### organize: filing the Apple Music way

1. **Pick the files to read**: the whole root, the paths given, or with `--since` only the folders where a file's ctime is newer than `last_run`, then the whole folder.
2. **Read the tags through the metadata cache and compute the target** (see [naming-rules.md](naming-rules.md)). A file without enough tags is `SKIPPED` and its folder blocked.
3. **Hold back what is doubtful**: an album whose tracks disagree on the compilation flag, or a target already taken, is a `CONFLICT` and stays where it is.
4. **Leave alone what is already in place**, tolerating accents (NFC/NFD), case and truncated names, unless a folder spelled exactly as the target exists next to the variant: the file then joins it.
5. **Put the MP4 index first**: an `.m4a` file whose `moov` atom comes after `mdat` is rewritten in place with `moov` first, without re-encoding and without a journal entry, so Lyrion streams it as `alc`.
6. **Move files one by one**, each move written to the journal right after it happens, so an interrupted pass can still be undone. Side files follow their album only when every track of the folder goes to the same place and none was held back.
7. **Carry the Lyrion history over** for the moved files (see [lyrion.md](lyrion.md)); a failure leaves the moves done and exits with code 5.

`last_run` only moves forward after a complete pass over the whole root.

### enrich: only what is missing or off

With `--apply`, the album folders are handed in batches of 20 to four modules run as subprocesses, in this order:

1. **`enrich.genres`** rewrites each genre to the spelling of the validated list (`genres.txt` in the state folder), aliases included. A genre the list does not know is reported `UNKNOWN` and left as is.
2. **`enrich.replaygain`** measures the files without gain with ffmpeg (EBU R128) and writes ReplayGain 2.0 tags referenced to -18 LUFS.
3. **`enrich.covers`** embeds `folder.jpg` into the tracks whose embedded cover differs from it, byte for byte.
4. **`enrich.lyrics`** looks up the files without lyrics (LRCLIB, then NetEase, Musixmatch if enabled) and writes them only when the provider's title, artist and duration (within 3 s) match the file. A file looked up in vain is recorded in `lyrics-misses.db` and skipped for `LYRICS_RETRY_DAYS` (30 by default) unless its artist, title or album changes; `--retry`, which the dashboard's album page uses, looks it up anyway.

On a stop request the current batch finishes and the rest waits for the next pass. `split` and `dedupe` then run over the whole library. A complete pass then drops the files gone from the library from the metadata cache.

A dry run reads only the metadata cache and the library's folders, never the network nor ffmpeg, and calls each module once over the whole library. What is decided locally is shown change by change: the genres the list aligns, the covers to embed, the credits split and the aliases applied. What needs a lookup or a measure is counted, with the time it would take: the albums to search on iTunes (3 s each, Apple's rate limit; the answers already kept in `genre_lookup.json` are used), the files to measure (their duration at 100 times real time per CPU) and the files whose lyrics to look up (1.5 s each, the `--delay` of 0.5 s included). The summary adds those times up, tool by tool. These rates are rough: a slow disk, a busy provider or a NAS CPU can double them. `enrich.lyrics --look-up`, which the album page uses, looks the lyrics up in a dry run anyway.

### split: credits holding several names

1. **Read the credits through the metadata cache**: album artist, artist and composer of every file under the paths given (the whole root by default).
2. **Gather the known names** from every file of the library in the cache: each album artist, and each artist or composer without a comma or slash, plus the names the decisions file vouches for.
3. **Settle each credit holding a comma or slash** (see [splitting.md](splitting.md)): a decision wins; a known name is kept (`GUARDED` when its fragments are all known too); a credit whose fragments are all known is split; any other waits for a decision.
4. **Rewrite the tags** with `--apply`, each change journaled with its old value, and write the credits newly awaiting a decision to the decisions file. `--review` asks about them instead, writing the decisions file only.

### dedupe: one artist spelled several ways

1. **Read the credits through the metadata cache**, cut on `;` as Lyrion does, each name rewritten by the aliases file.
2. **Group the likely duplicates** (see [dedupe.md](dedupe.md)): same name once normalized, same MusicBrainz id, one typo apart, or a partial name. Names with different MusicBrainz ids, or declared different, are never grouped.
3. **Rewrite the variants** the aliases file names with `--apply`, each change journaled. `--review` asks about the groups instead, writing the aliases file only. A renamed album artist is moved by the next `organize` pass.

### tag: fields rewritten, then filed again

1. **Read each track** given (or under each folder given) directly, not through the cache, and plan its changes: a value already there is left, a genre takes the validated list's spelling, and an edit made against values the track no longer holds (`seen`, which the dashboard passes) is `REFUSED`. Tracks outside the library are skipped.
2. **Write the fields** with `--apply` through `media/fields.py`, each change journaled with its old values.
3. **File the folders of the edited tracks** with `organize_pass`, in the same journal, all or nothing like import. A dry run hands organize the tags as they will be (`Options.overrides`), so it lists the exact moves, conflicts included.

### undo

`undo JOURNAL` replays a journal backwards: moves, then tag changes. A tag change followed by a move of the same file, as `tag` journals them, is checked where the file went, and restored where the undone moves bring it back. A file that is missing, whose original place is taken, or whose tag changed since, is `REFUSED` rather than forced. The restores are journaled too, and carried over to Lyrion like any move.

### The metadata cache

The metadata cache (`media/metadata_cache.py`, `metadata.db` in the state folder) holds one snapshot per music file: its text tags, duration, whether it has lyrics and ReplayGain, the hash of its embedded cover, and for an MP4 file whether its `moov` atom comes after `mdat`. A snapshot is read again only when the file's size, mtime or ctime changed, so after one full read (`scan`, or any first pass) `organize` and the enrichment tools only open the files that changed. Files outside the library, such as an Inbox item being imported, are read directly and never cached. The tools run as separate processes, possibly at the same time as a manual run: SQLite serializes their writes. The cache is state, not the library, so dry runs fill it too. `scan --force` and `enrich --rescan` read every file again; the weekly `enrich` of `watch` runs with `--rescan`, so a stale snapshot never outlives a week.

### The run history

Each pass is kept in the state folder, whichever way it was started: one row in `runs.db` (command, origin `cli`, `watch` or `web`, for `watch` its trigger: `start`, `inbox`, `daily` or `schedule`, apply, start and end, exit code, summary, host and pid, and the options of a dashboard request) and its output in `runs/<id>.log`, the 200 latest only. The commands' `main` and the jobs of `watch` run their pass through `runs.recorded`, which tees the pass's `Log` into the file; the passes called within it (`scan` under `enrich --rescan`, `organize_pass` under `import`, `split_pass` and `dedupe_pass`) write to the same run, and `enrich` passes on the output of its tool processes line by line. The summary is the `key: value` lines of the last `--- … ---` section that has any. Each row also keeps the host and pid of its process: a run with no end whose process is gone from this host shows as interrupted, and `watch` closes the unfinished runs of its host as it starts, since its pid may be the one of the watch killed before it. A pass that found the lock taken did nothing and is not kept; one that crashed keeps its traceback and no exit code. The `--review` modes are not recorded: they only write the decisions and aliases files.

## Conventions

- Everything is a dry run without `--apply`.
- `organize`, `import`, `enrich`, `split` and `dedupe` each parse their arguments in `main(argv)` into an `Options` dataclass, then do the work in `run(opts, log)`. Commands call each other through these functions, never by building an argument list: `watch` builds the options of each pass once, at startup.
- Only one `--apply` pass runs at a time, through a lock in the state folder that `run` takes. The lock is not reentrant: `import` files through `organize_pass`, the unlocked function under `organize.run`, while it holds the lock itself, and `import` and `enrich` call `split_pass` and `dedupe_pass` the same way.
- A stop request (SIGTERM from `docker stop`, or Ctrl-C) lets the current file operation finish, then stops. A second Ctrl-C stops at once.
- Exit codes: 0 OK, 2 something was skipped or failed, 3 another pass holds the lock, 4 stopped before the end, 5 the Lyrion carry-over failed.
- Imports inside the package are absolute: `from music_librarian.core.lock import STOP`.
- The tests mirror the package under `tests/`.
