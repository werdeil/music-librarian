"""dedupe --review: the groups of likely duplicates, one at a time, largest first.

Each answer is written to the aliases file at once, so a review can stop at any point
and resume where it left off."""
from itertools import combinations

from music_librarian.core.lock import STOP
from music_librarian.core.log import count
from music_librarian.dedupe import aliases as aliases_file
from music_librarian.dedupe.aliases import canonical
from music_librarian.dedupe.similar import PARTIAL, words
from music_librarian.split.rules import key

CHOICES = "e edit · d different · n later · u undo · q quit"


def suggestion(names, reasons):
    """The spelling to keep: the most frequent, of several words when a partial name is involved."""
    full = [n for n in names if len(words(n)) > 1] if PARTIAL in reasons else []
    return (full or names)[0]


def pending(names, aliases, different):
    """The names of a group that still disagree, or [] when the file settles it."""
    left = list(dict.fromkeys(canonical(n, aliases) for n in names))
    if all(frozenset((key(a), key(b))) in different for a, b in combinations(left, 2)):
        return []
    return left


def show(log, position, total, names, reasons, counts):
    log(f"REVIEW   [{position}/{total}] {', '.join(sorted(reasons))}")
    for i, n in enumerate(names, 1):
        log(f"         {i}  {n}  ({count(counts.get(n, 0), 'tag')})")


def answer(ask, names, hint):
    """The entries to write, or one of "later", "undo", "quit"; None to ask again."""
    try:
        reply = ask(f"         [Enter] → {hint} · 1-{len(names)} pick · {CHOICES} > ").strip()
        if reply.lower() == "e":
            reply = ask("         name to write > ").strip()
            return [(n, "=>", reply) for n in names if n != reply] if reply else None
    except (EOFError, KeyboardInterrupt):
        return "quit"
    if reply.lower() in ("n", "u", "q"):
        return {"n": "later", "u": "undo", "q": "quit"}[reply.lower()]
    if reply.lower() == "d":
        return [(a, "!=", b) for a, b in combinations(names, 2)]
    if reply == "" or (reply.isdigit() and 1 <= int(reply) <= len(names)):
        target = names[int(reply) - 1] if reply else hint
        return [(n, "=>", target) for n in names if n != target]
    return None


def review(queue, counts, path, ask, log):
    """Ask about each (names, reasons) of queue the aliases file does not settle yet.

    Returns the number of answers kept."""
    history, i = [], 0
    while i < len(queue) and not STOP.is_set():
        aliases, different = aliases_file.load(path, log)
        names = pending(queue[i][0], aliases, different)
        if len(names) < 2:
            i += 1
            continue
        show(log, i + 1, len(queue), names, queue[i][1], counts)
        entries = answer(ask, names, suggestion(names, queue[i][1]))
        if entries == "quit":
            break
        if entries == "undo":
            if history:
                i, done = history.pop()
                aliases_file.update(path, drop=[aliases_file.identity(e) for e in done])
                log("UNDONE   previous answer")
            continue
        if entries is None:
            continue
        if entries != "later":
            aliases_file.update(path, add=entries)
            history.append((i, entries))
        i += 1
    return len(history)
