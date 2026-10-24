"""
enrich — enrichment pass over the whole library (weekly).

Each tool only handles what is missing:
  - replaygain: enrich/replaygain.py, only the files without gain;
  - covers:     enrich/covers.py, only where folder.jpg differs from the embedded
                cover;
  - lyrics:     enrich/lyrics.py, only the files without lyrics (this is what
                catches lyrics published after the import).

Album folders are handed to the tools in batches: on SIGTERM or Ctrl-C, the
current batch completes (no tag write is cut short) and the rest is done on
the next pass. With --apply the lock is held: no organize or import while tags
are being rewritten.

  music-librarian enrich          # dry run: what each tool would do
  music-librarian enrich --apply

The end of a full --apply pass is recorded in state/enrich_last_run.

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

from music_librarian import importer
from music_librarian.core.files import AUDIO_EXT
from music_librarian.core.lock import EXIT_LOCKED, EXIT_STOPPED, STOP, Locked, handle_stop_signals, locked
from music_librarian.core.log import Log
from music_librarian.core.state import read_stamp, state_path, write_stamp

LAST_RUN = "enrich_last_run"
BATCH = 20  # folders per tool call: a lyrics batch takes one to two minutes
DRY = {"replaygain": "--dry-run", "covers": "--dry-run", "lyrics": "--dry-run"}


def album_dirs(root: Path):
    """Folders holding at least one audio file, in order."""
    out = []
    for d, dirnames, files in os.walk(root):
        dirnames[:] = sorted(n for n in dirnames if not n.startswith("."))
        if any(not f.startswith(".") and os.path.splitext(f)[1].lower() in AUDIO_EXT
               for f in files):
            out.append(Path(d))
    return out


@dataclass
class Options:
    root: Path
    apply: bool = False
    state_dir: Path = field(default_factory=state_path)
    batch: int = BATCH

    def __post_init__(self):
        self.root = Path(self.root).resolve()


def last_run(state_dir: Path):
    return read_stamp(state_dir, LAST_RUN)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Enrich the whole library.")
    root_env = os.environ.get("MUSIC_LIBRARIAN_ROOT")
    ap.add_argument("--root", default=root_env, required=not root_env)
    ap.add_argument("--apply", action="store_true", help="write the tags (otherwise dry run)")
    ap.add_argument("--state-dir", default=os.environ.get("MUSIC_LIBRARIAN_STATE"))
    ap.add_argument("--batch", type=int, default=BATCH, help="folders per tool call")
    args = ap.parse_args(argv)
    opts = Options(root=args.root, apply=args.apply, state_dir=state_path(args.state_dir),
                   batch=args.batch)
    return run(opts, Log())


def run(opts: Options, log):
    """An enrichment pass, under the lock with `apply`."""
    try:
        with locked(opts.state_dir) if opts.apply else contextlib.nullcontext():
            return enrich_all(opts, log)
    except Locked as e:
        log(f"LOCKED   another pass is running ({e}), giving up")
        return EXIT_LOCKED


def enrich_all(opts, log):
    started = time.time()
    dirs = album_dirs(opts.root)
    batches = [dirs[i:i + opts.batch] for i in range(0, len(dirs), opts.batch)]
    log(f"[{'APPLY' if opts.apply else 'DRY-RUN'}] enriching {opts.root}: "
        f"{len(dirs)} folders, {len(batches)} batches")
    failed = []
    for name, cmd in importer.enrichers():
        if not opts.apply:
            cmd = [*cmd, DRY[name]]
        log(f"ENRICH   ({name})")
        for i, batch in enumerate(batches, 1):
            if STOP.is_set():
                log(f"STOP     requested: {name} stopped at batch {i}/{len(batches)}, "
                    f"resumed on the next pass")
                return EXIT_STOPPED
            try:
                r = subprocess.run([*cmd, *map(str, batch)], check=False)
                ok = r.returncode in (0, EXIT_STOPPED)   # what a stopped tool left is done next pass
            except OSError as e:
                log(f"         {e}")
                ok = False
            if not ok:
                log(f"FAILED   {name}, batch {i}/{len(batches)} ({batch[0]} …)")
                failed.append(name)
    log("\n--- Enrichment summary ---")
    log(f"{'folders':>20}: {len(dirs)}")
    if failed:
        log(f"{'failed batches':>20}: {len(failed)} ({', '.join(sorted(set(failed)))})")
    if opts.apply:
        write_stamp(opts.state_dir, LAST_RUN, started)
    return 2 if failed else 0


if __name__ == "__main__":
    handle_stop_signals()
    sys.exit(main())
