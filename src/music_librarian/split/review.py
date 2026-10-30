"""split --review: the credits awaiting a decision, one at a time, most frequent first,
then the protected ones that look like a collaboration (kept unless the answer splits them).

Each answer is written to the decisions file at once, so a review can stop at any
point and resume where it left off. An answer vouches for names, which can settle
the credits further down: those are not asked about.
"""
from music_librarian.core.lock import STOP
from music_librarian.core.log import count
from music_librarian.split import decisions
from music_librarian.split.rules import (KEEP, PENDING, PROTECTED, SPLIT, decided_names, fragments, key,
                                         names_of, resolve, suggest)

CHOICES = "s split · k keep · e edit · n later · u undo · q quit"


def show(log, position, total, item, known):
    credit, tracks, example, status = item
    parts = fragments(credit)
    seen = [p for p in parts if key(p) in known]
    log(f"REVIEW   [{position}/{total}] {credit}  ({count(tracks, 'track')})"
        + ("  album artist, kept unless split here" if status == PROTECTED else ""))
    log(f"         split: {' · '.join(parts)}"
        + (f"  (known: {', '.join(seen)})" if seen else ""))
    log(f"         e.g. {example}")


def answer(ask, hint):
    """The decision typed, or one of "later", "undo", "quit"; None to ask again."""
    try:
        reply = ask(f"         [Enter] {hint} · {CHOICES} > ").strip().lower()
        if reply == "e":
            names = names_of(ask("         names, separated by ';' > "))
            return "; ".join(names) if names else None
    except (EOFError, KeyboardInterrupt):
        return "quit"
    return {"": hint, "s": SPLIT, "k": KEEP, "n": "later", "u": "undo", "q": "quit"}.get(reply)


def review(queue, known, decided, path, ask, log):
    """Ask about each (credit, tracks, example, status) of queue not decided yet.

    Writes each answer to the decisions file at `path` and updates `decided`.
    Returns the number of answers kept."""
    history, later, i = [], set(), 0
    while i < len(queue) and not STOP.is_set():
        credit = queue[i][0]
        current = known | decided_names(decided)
        status = resolve(credit, current, decided)[1]
        if key(credit) in later or status not in (PENDING, PROTECTED):
            i += 1
            continue
        show(log, i + 1, len(queue), queue[i], current)
        decision = answer(ask, KEEP if status == PROTECTED else suggest(credit, current))
        if decision == "quit":
            break
        if decision == "later":
            later.add(key(credit))
        elif decision == "undo":
            if history:
                i, previous, before = history.pop()
                restore(decided, path, previous, before)
                log(f"UNDONE   {previous}")
            continue
        elif decision is not None:
            history.append((i, credit, decided.get(key(credit))))
            decided[key(credit)] = decision
            decisions.record(path, credit, decision)
        else:
            continue
        i += 1
    return len(history)


def restore(decided, path, credit, before):
    if before is None:
        decided.pop(key(credit), None)
    else:
        decided[key(credit)] = before
    decisions.record(path, credit, before)
