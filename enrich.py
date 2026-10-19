#!/usr/bin/env python3
"""
enrich — passage d'enrichissement sur toute la bibliothèque (hebdomadaire).

Remplace les crons de nuit de l'hôte. Chaque outil ne traite que ce qui manque :
  - replaygain : r128gain -s, seulement les fichiers sans gain ;
  - pochettes  : embed_covers.py, seulement si folder.jpg diffère de la pochette
                 intégrée ;
  - paroles    : embed_lyrics.py, seulement les fichiers sans paroles (c'est ce
                 qui rattrape les paroles publiées après l'import).

Les dossiers d'album sont passés aux outils par lots : sur SIGTERM ou Ctrl-C,
le lot en cours se termine (aucune écriture de tag coupée), le reste est fait
au passage suivant. En --apply, le verrou est pris : pas de rangement ni
d'import pendant que les tags sont réécrits.

  enrich.py                       # dry-run : ce que chaque outil ferait
  enrich.py --apply

La fin d'un passage complet en --apply est notée dans state/enrich_last_run.

Codes retour : 0 = OK, 2 = un outil en échec, 3 = un autre passage est en cours,
4 = arrêté avant la fin.
"""
import argparse
import contextlib
import os
import subprocess
import sys
import time
from pathlib import Path

import import_inbox
import music_ranger
from music_ranger import AUDIO_EXT, EXIT_LOCKED, EXIT_STOPPED, STOP, Locked, Log, locked

LAST_RUN = "enrich_last_run"
BATCH = 20  # dossiers par appel d'outil : un lot de paroles prend une à deux minutes
DRY = {"replaygain": "--dry-run", "pochettes": "--dry-run", "paroles": "--dry-run"}


def album_dirs(root: Path):
    """Dossiers contenant au moins un fichier audio, dans l'ordre."""
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
    ap.add_argument("--r128gain", default=import_inbox.R128GAIN)
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    state_dir = music_ranger.state_path(args.state_dir)
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
    for name, cmd in import_inbox.enrichers(args):
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
    music_ranger.handle_stop_signals()
    sys.exit(main())
