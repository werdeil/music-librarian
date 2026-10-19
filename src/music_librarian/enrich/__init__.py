"""
enrich — enrichment pass over the whole library (weekly).

Each tool only handles what is missing:
  - replaygain: r128gain -s, only the files without gain;
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
from pathlib import Path

from music_librarian import importer
from music_librarian.core.files import AUDIO_EXT
from music_librarian.core.lock import EXIT_LOCKED, EXIT_STOPPED, STOP, Locked, handle_stop_signals, locked
from music_librarian.core.log import Log
from music_librarian.core.state import state_path

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


def last_run(state_dir: Path):
    try:
        return float((state_dir / LAST_RUN).read_text().strip())
    except (OSError, ValueError):
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Enrich the whole library.")
    root_env = os.environ.get("MUSIC_LIBRARIAN_ROOT")
    ap.add_argument("--root", default=root_env, required=not root_env)
    ap.add_argument("--apply", action="store_true", help="write the tags (otherwise dry run)")
    ap.add_argument("--state-dir", default=os.environ.get("MUSIC_LIBRARIAN_STATE"))
    ap.add_argument("--batch", type=int, default=BATCH, help="folders per tool call")
    ap.add_argument("--r128gain", default=importer.R128GAIN)
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    state_dir = state_path(args.state_dir)
    log = Log()
    try:
        with locked(state_dir) if args.apply else contextlib.nullcontext():
            return enrich_all(args, root, state_dir, log)
    except Locked as e:
        log(f"LOCKED   another pass is running ({e}), giving up")
        return EXIT_LOCKED


def enrich_all(args, root, state_dir, log):
    started = time.time()
    dirs = album_dirs(root)
    batches = [dirs[i:i + args.batch] for i in range(0, len(dirs), args.batch)]
    log(f"[{'APPLY' if args.apply else 'DRY-RUN'}] enriching {root}: "
        f"{len(dirs)} folders, {len(batches)} batches")
    failed = []
    for name, cmd in importer.enrichers(args):
        if not args.apply:
            cmd = [*cmd, DRY[name]]
        log(f"ENRICH   ({name})")
        for i, batch in enumerate(batches, 1):
            if STOP.is_set():
                log(f"STOP     requested: {name} stopped at batch {i}/{len(batches)}, "
                    f"resumed on the next pass")
                return EXIT_STOPPED
            try:
                r = subprocess.run([*cmd, *map(str, batch)], check=False)
                ok = r.returncode == 0
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
    if args.apply:
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / LAST_RUN).write_text(f"{started}\n")
    return 2 if failed else 0


if __name__ == "__main__":
    handle_stop_signals()
    sys.exit(main())
