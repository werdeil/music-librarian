"""
enrich — enrichment pass over the whole library (weekly).

Each tool only handles what is missing or off:
  - genres:     enrich/genres.py, only the files whose genre is not in the
                validated list's spelling (unknown genres are reported);
  - replaygain: enrich/replaygain.py, only the files without gain;
  - covers:     enrich/covers.py, only where folder.jpg differs from the embedded
                cover;
  - lyrics:     enrich/lyrics.py, only the files without lyrics (this is what
                catches lyrics published after the import);
  - split:      split/, the credits holding several names, decisions taken in
                state/split-decisions.txt included (--no-split to leave them);
  - dedupe:     dedupe/, the spellings decided in state/artist-aliases.txt
                (--no-dedupe to leave them).

Album folders are handed to the tools in batches: on SIGTERM or Ctrl-C, the
current batch completes (no tag write is cut short) and the rest is done on
the next pass. With --apply the lock is held: no organize or import while tags
are being rewritten.

  music-librarian enrich          # dry run: what each tool would do
  music-librarian enrich --apply
  music-librarian enrich --rescan # first read the whole library again (scan --force)

The tools read the tags through the metadata cache (media/metadata_cache.py),
so a pass only opens the files that changed; a complete pass drops the files
gone from the library from it. The end of a full --apply pass is recorded in
state/enrich_last_run.

Exit codes: 0 = OK, 2 = a tool failed, 3 = another pass is running,
4 = stopped before the end.
"""
import argparse
import contextlib
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from music_librarian import dedupe, scan, split
from music_librarian.core import arguments
from music_librarian.core.lock import EXIT_LOCKED, EXIT_STOPPED, STOP, Locked, handle_stop_signals, locked
from music_librarian.core.log import Log, count
from music_librarian.core.runs import recorded
from music_librarian.core.state import read_stamp, state_path, write_stamp
from music_librarian.enrich.common import album_folders
from music_librarian.media.metadata_cache import MetadataCache

LAST_RUN = "enrich_last_run"
BATCH = 20  # folders per tool call: a lyrics batch takes one to two minutes
DRY = {"genres": "--dry-run", "replaygain": "--dry-run", "covers": "--dry-run", "lyrics": "--dry-run"}


def enrichers():
    """(name, command) called with the target folders as arguments."""
    return [
        (name, [sys.executable, "-m", f"music_librarian.enrich.{name}"])
        for name in ("genres", "replaygain", "covers", "lyrics")
    ]


def run_tool(cmd, env, log):
    """The tool's exit code, its output passed on to `log` line by line."""
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                          encoding="utf-8", errors="backslashreplace") as p:
        for line in p.stdout:
            log(line.rstrip("\n"))
    return p.returncode


def album_dirs(root: Path):
    """(folders holding at least one music file, in order, and those files)."""
    found = list(album_folders([str(root)]))
    return [Path(d) for d, _files in found], [f for _d, files in found for f in files]


@dataclass
class Options:
    root: Path
    apply: bool = False
    state_dir: Path = field(default_factory=state_path)
    batch: int = BATCH
    rescan: bool = False
    split: bool = True
    dedupe: bool = True

    def __post_init__(self):
        self.root = Path(self.root).resolve()


def last_run(state_dir: Path):
    return read_stamp(state_dir, LAST_RUN)


def main(argv=None):
    ap = arguments.parser("enrich", "Enrich the whole library.")
    arguments.add_root(ap)
    arguments.add_apply(ap, "write the tags")
    ap.add_argument("--batch", type=int, default=BATCH, help=f"folders per tool call (default: {BATCH})")
    ap.add_argument("--rescan", action="store_true",
                    help="first read every file of the library again into the metadata cache")
    ap.add_argument("--no-split", action="store_true", help="without splitting the credits")
    ap.add_argument("--no-dedupe", action="store_true", help="without applying the artist aliases")
    arguments.add_state_dir(ap)
    arguments.add_syslog(ap)
    args = ap.parse_args(argv)
    opts = Options(root=args.root, apply=args.apply, state_dir=state_path(args.state_dir),
                   batch=args.batch, rescan=args.rescan, split=not args.no_split,
                   dedupe=not args.no_dedupe)
    log = Log(args.syslog)
    return recorded(lambda: run(opts, log), log, opts.state_dir, "enrich", opts.apply)


def run(opts: Options, log):
    """An enrichment pass, under the lock with `apply`."""
    try:
        with locked(opts.state_dir) if opts.apply else contextlib.nullcontext():
            return enrich_all(opts, log)
    except Locked as e:
        log(f"LOCKED   another pass is running ({e}), giving up")
        return EXIT_LOCKED


def tool_env(opts):
    return {**os.environ, "MUSIC_LIBRARIAN_STATE": str(opts.state_dir), "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1"}


def tool_pass(name, folders, opts: Options, log, *flags):
    """One tool of enrichers() on `folders`, under the lock with `apply`; its exit code."""
    cmd = [*dict(enrichers())[name], *flags, *([] if opts.apply else [DRY[name]])]
    try:
        with locked(opts.state_dir) if opts.apply else contextlib.nullcontext():
            log(f"[{'APPLY' if opts.apply else 'DRY-RUN'}] {name}: {', '.join(map(str, folders))}")
            return run_tool([*cmd, *map(str, folders)], tool_env(opts), log)
    except Locked as e:
        log(f"LOCKED   another pass is running ({e}), giving up")
        return EXIT_LOCKED


def enrich_all(opts, log):
    started = time.time()
    if opts.rescan and scan.run(opts.root, opts.state_dir, log, force=True) == EXIT_STOPPED:
        return EXIT_STOPPED
    dirs, files = album_dirs(opts.root)
    env = tool_env(opts)
    batches = [dirs[i:i + opts.batch] for i in range(0, len(dirs), opts.batch)]
    log(f"[{'APPLY' if opts.apply else 'DRY-RUN'}] enriching {opts.root}: "
        f"{count(len(dirs), 'folder')}, {count(len(batches), 'batch', 'batches')}")
    failed = []
    for name, cmd in enrichers():
        if not opts.apply:
            cmd = [*cmd, DRY[name]]
        log(f"ENRICH   ({name})")
        for i, batch in enumerate(batches, 1):
            if STOP.is_set():
                log(f"STOP     requested: {name} stopped at batch {i}/{len(batches)}, "
                    f"resumed on the next pass")
                return EXIT_STOPPED
            try:
                # what a stopped tool left is done next pass
                ok = run_tool([*cmd, *map(str, batch)], env, log) in (0, EXIT_STOPPED)
            except OSError as e:
                log(f"         {e}")
                ok = False
            if not ok:
                log(f"FAILED   {name}, batch {i}/{len(batches)} ({batch[0]} …)")
                failed.append(name)
    for name, wanted, tag_pass, pass_options in (
            ("split", opts.split, split.split_pass, split.Options),
            ("dedupe", opts.dedupe, dedupe.dedupe_pass, dedupe.Options)):
        if not wanted:
            continue
        log(f"ENRICH   ({name})")
        code = tag_pass(pass_options(root=opts.root, apply=opts.apply, state_dir=opts.state_dir), log)
        if code == EXIT_STOPPED:
            return EXIT_STOPPED
        if code:
            failed.append(name)
    with MetadataCache(opts.state_dir) as cache:
        removed = cache.prune(opts.root, files)
    log("\n--- Enrichment summary ---")
    log(f"{'folders':>20}: {len(dirs)}")
    if removed:
        log(f"{'gone from cache':>20}: {removed}")
    if failed:
        log(f"{'failed batches':>20}: {len(failed)} ({', '.join(sorted(set(failed)))})")
    if opts.apply:
        write_stamp(opts.state_dir, LAST_RUN, started)
    return 2 if failed else 0


if __name__ == "__main__":
    handle_stop_signals()
    sys.exit(main())
