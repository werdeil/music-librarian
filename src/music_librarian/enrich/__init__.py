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
the next pass. With --apply the lock is held: no range or import while tags
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

from .. import importer
from ..core.files import AUDIO_EXT
from ..core.lock import EXIT_LOCKED, EXIT_STOPPED, STOP, Locked, handle_stop_signals, locked
from ..core.log import Log
from ..core.state import state_path

LAST_RUN = "enrich_last_run"
BATCH = 20  # folders per tool call: a lyrics batch takes one to two minutes
DRY = {"replaygain": "--dry-run", "pochettes": "--dry-run", "paroles": "--dry-run"}


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
    ap = argparse.ArgumentParser(description="Enrichit toute la bibliothèque.")
    root_env = os.environ.get("MUSIC_LIBRARIAN_ROOT")
    ap.add_argument("--root", default=root_env, required=not root_env)
    ap.add_argument("--apply", action="store_true", help="écrire les tags (sinon dry-run)")
    ap.add_argument("--state-dir", default=os.environ.get("MUSIC_LIBRARIAN_STATE"))
    ap.add_argument("--batch", type=int, default=BATCH, help="dossiers par appel d'outil")
    ap.add_argument("--r128gain", default=importer.R128GAIN)
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    state_dir = state_path(args.state_dir)
    log = Log()
    try:
        with locked(state_dir) if args.apply else contextlib.nullcontext():
            return enrich_all(args, root, state_dir, log)
    except Locked as e:
        log(f"VERROU   un autre passage est en cours ({e}), abandon")
        return EXIT_LOCKED


def enrich_all(args, root, state_dir, log):
    started = time.time()
    dirs = album_dirs(root)
    batches = [dirs[i:i + args.batch] for i in range(0, len(dirs), args.batch)]
    log(f"[{'APPLY' if args.apply else 'DRY-RUN'}] enrichissement de {root} : "
        f"{len(dirs)} dossiers, {len(batches)} lots")
    failed = []
    for name, cmd in importer.enrichers(args):
        if not args.apply:
            cmd = [*cmd, DRY[name]]
        log(f"ENRICHIR ({name})")
        for i, batch in enumerate(batches, 1):
            if STOP.is_set():
                log(f"ARRÊT    demandé : {name} arrêté au lot {i}/{len(batches)}, "
                    f"repris au prochain passage")
                return EXIT_STOPPED
            try:
                r = subprocess.run([*cmd, *map(str, batch)], check=False)
                ok = r.returncode == 0
            except OSError as e:
                log(f"         {e}")
                ok = False
            if not ok:
                log(f"ÉCHEC    {name}, lot {i}/{len(batches)} ({batch[0]} …)")
                failed.append(name)
    log("\n--- Résumé enrichissement ---")
    log(f"{'dossiers':>20} : {len(dirs)}")
    if failed:
        log(f"{'lots en échec':>20} : {len(failed)} ({', '.join(sorted(set(failed)))})")
    if args.apply:
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / LAST_RUN).write_text(f"{started}\n")
    return 2 if failed else 0


if __name__ == "__main__":
    handle_stop_signals()
    sys.exit(main())
