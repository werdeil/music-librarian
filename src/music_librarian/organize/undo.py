"""Undo a pass by replaying its journal backwards."""
import json
import shlex
import time
from pathlib import Path

from music_librarian.core.files import prune_empty
from music_librarian.core.journal import Journal, move_file
from music_librarian.core.lock import EXIT_STOPPED, STOP
from music_librarian.integrations import lyrion


def undo(args, root, state_dir, log, db=None):
    jpath = Path(args.undo)
    recs = [json.loads(line) for line in jpath.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    moves = [(Path(r["dst"]), Path(r["src"])) for r in reversed(recs)
             if r.get("type") == "move"]
    mode = "APPLY" if args.apply else "DRY-RUN"
    log(f"[{mode}] annulation de {jpath} ({len(moves)} déplacements)")
    stopped = False

    todo, refused = [], 0
    for cur, orig in moves:
        if not cur.exists():
            log(f"REFUSÉ   (fichier absent)  {cur}")
            refused += 1
        elif orig.exists():
            log(f"REFUSÉ   (place d'origine prise)  {orig}")
            refused += 1
        else:
            log(f"RESTAURER {cur}\n          → {orig}")
            todo.append((cur, orig))

    journal = Journal(state_dir, dict(root=str(root), undo_of=str(jpath),
                                      started=time.time()))
    pruned = 0
    done = []
    if args.apply:
        for i, (cur, orig) in enumerate(todo):
            if STOP.is_set():
                log(f"ARRÊT    demandé : {len(todo) - i} restauration(s) non faite(s)")
                stopped = True
                break
            move_file(cur, orig, journal)
            done.append((cur, orig))
        for d in sorted({cur.parent for cur, _ in todo}, key=lambda d: -len(d.parts)):
            pruned += prune_empty(d, {root})
    journal.close()

    log("\n--- Résumé ---")
    log(f"{'à restaurer':>20} : {len(todo)}")
    if refused:
        log(f"{'refusés':>20} : {refused}")
    if pruned:
        log(f"{'dossiers supprimés':>20} : {pruned}")
    if journal.path:
        log(f"{'journal':>20} : {journal.path}")
    lyrion_ok = lyrion.carry_over(db, done if args.apply else todo, args.apply, log)
    if not lyrion_ok and journal.path:
        log(f"{'reprise Lyrion':>20} : music-librarian range --lyrion-replay "
            f"{shlex.quote(str(journal.path))} --apply")
    return EXIT_STOPPED if stopped else 2 if refused else 0 if lyrion_ok else lyrion.EXIT_LYRION
