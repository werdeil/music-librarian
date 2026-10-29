"""Undo a pass by replaying its journal backwards: moves, then tag changes."""
import json
import shlex
import time
from collections import defaultdict
from pathlib import Path

from music_librarian.core.files import prune_empty
from music_librarian.core.journal import Journal, move_file
from music_librarian.core.lock import EXIT_STOPPED, STOP
from music_librarian.integrations import lyrion
from music_librarian.media import fields


def values(v):
    """A journaled value as a field's values: a list as is, a string (split, dedupe) as one value."""
    return v if isinstance(v, list) else [v] if v else []


def shown(v):
    return "; ".join(values(v)) or "(none)"


def plan_tags(recs, log):
    """Tag changes that can be undone, as (path where the file is now, field, written, original):
    the file still holds the value written. A file moved after its tag change, in the same
    journal, is read where it went."""
    todo, refused = [], 0
    moves = defaultdict(list)   # source -> [(position in the journal, destination)]
    for i, r in enumerate(recs):
        if r.get("type") == "move":
            moves[Path(r["src"])].append((i, Path(r["dst"])))
    for i in reversed(range(len(recs))):
        r = recs[i]
        if r.get("type") != "tag":
            continue
        path, at = Path(r["path"]), i
        while (later := next(((j, dst) for j, dst in moves.get(path, ()) if j > at), None)):
            at, path = later
        try:
            current = fields.read(path)
        except Exception:
            current = None
        if current is None:
            log(f"REFUSED  (file missing or unreadable)  {path}")
            refused += 1
        elif current[r["field"]] != values(r["new"]):
            log(f"REFUSED  ({r['field']} changed since)  {path}")
            refused += 1
        else:
            log(f"RESTORE  {r['field']} of {path}\n         {shown(r['new'])} → {shown(r['old'])}")
            todo.append((path, r["field"], r["new"], r["old"]))
    return todo, refused


def restore_tags(tags, done, journal, log):
    """Restore the tags where each file is once the moves in `done` (current, original) are
    undone; (stopped, failed writes)."""
    back = dict(done)
    failed = 0
    for i, (path, field, cur, orig) in enumerate(tags):
        if STOP.is_set():
            log(f"STOP     requested: {len(tags) - i} tag restore(s) not done")
            return True, failed
        for _ in range(len(back)):   # bounded: a file moved there and back makes a cycle
            path = back.get(path, path)
        try:
            fields.write(path, field, values(orig))
        except Exception as e:
            log(f"ERROR    ({e})  {path}")
            failed += 1
            continue
        journal.tag(path, field, cur, orig)
    return False, failed


def undo(args, root, state_dir, log, db=None):
    jpath = Path(args.undo)
    recs = [json.loads(line) for line in jpath.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    moves = [(Path(r["dst"]), Path(r["src"])) for r in reversed(recs)
             if r.get("type") == "move"]
    mode = "APPLY" if args.apply else "DRY-RUN"
    log(f"[{mode}] undoing {jpath} ({len(moves)} moves)")
    stopped = False

    todo, refused = [], 0
    for cur, orig in moves:
        if not cur.exists():
            log(f"REFUSED  (file missing)  {cur}")
            refused += 1
        elif orig.exists():
            log(f"REFUSED  (original place taken)  {orig}")
            refused += 1
        else:
            log(f"RESTORE  {cur}\n         → {orig}")
            todo.append((cur, orig))
    tags, tags_refused = plan_tags(recs, log)
    refused += tags_refused

    journal = Journal(state_dir, dict(root=str(root), undo_of=str(jpath),
                                      started=time.time()))
    pruned = 0
    done = []
    if args.apply:
        for i, (cur, orig) in enumerate(todo):
            if STOP.is_set():
                log(f"STOP     requested: {len(todo) - i} restore(s) not done")
                stopped = True
                break
            move_file(cur, orig, journal)
            done.append((cur, orig))
        if not stopped:
            stopped, failed = restore_tags(tags, done, journal, log)
            refused += failed
        for d in sorted({cur.parent for cur, _ in todo}, key=lambda d: -len(d.parts)):
            pruned += prune_empty(d, {root})
    journal.close()

    log("\n--- Summary ---")
    log(f"{'to restore':>20}: {len(todo)}")
    if tags:
        log(f"{'tags to restore':>20}: {len(tags)}")
    if refused:
        log(f"{'refused':>20}: {refused}")
    if pruned:
        log(f"{'folders removed':>20}: {pruned}")
    if journal.path:
        log(f"{'journal':>20}: {journal.path}")
    lyrion_ok = lyrion.carry_over(db, done if args.apply else todo, args.apply, log)
    if not lyrion_ok and journal.path:
        log(f"{'Lyrion retry':>20}: music-librarian organize --lyrion-replay "
            f"{shlex.quote(str(journal.path))} --apply")
    return EXIT_STOPPED if stopped else 2 if refused else 0 if lyrion_ok else lyrion.EXIT_LYRION
