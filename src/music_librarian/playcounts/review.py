"""playcounts --review: the orphans awaiting an answer, one at a time, the most played first.

Each answer is written to the matches file at once, so a review can stop at any point and resume
where it left off."""
from music_librarian.core.lock import STOP
from music_librarian.core.log import count
from music_librarian.playcounts import decisions

CHOICES = "i ignore · u undo · q quit"


def answer(ask, matches):
    """The track picked, or one of "ignore", "later", "undo", "quit"; None to ask again."""
    try:
        reply = ask(f"         [Enter] later · 1-{len(matches)} pick · {CHOICES} > ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return "quit"
    if reply in ("", "i", "u", "q"):
        return {"": "later", "i": "ignore", "u": "undo", "q": "quit"}[reply]
    if reply.isdigit() and 1 <= int(reply) <= len(matches):
        return matches[int(reply) - 1]
    return None


def review(queue, suggest, path, ask, log):
    """Ask about each (orphan, its relative path) of queue; `suggest(path, plays)` gives its matches.

    Returns the number of answers kept."""
    history, i = [], 0
    while i < len(queue) and not STOP.is_set():
        orphan, rel = queue[i]
        matches = suggest(rel, orphan.plays)
        log(f"REVIEW   [{i + 1}/{len(queue)}] {rel}  ({count(orphan.plays, 'play')})")
        for n, (note, _, target, plays) in enumerate(matches, 1):
            log(f"         {n}  {note:3.0f} %  {target}  ({count(plays, 'play')})")
        picked = answer(ask, matches)
        if picked == "quit":
            break
        if picked == "undo":
            if history:
                i = history.pop()
                decisions.update(path, drop=[queue[i][0].key])
                log("UNDONE   previous answer")
            continue
        if picked is None:
            continue
        if picked == "ignore":
            decisions.update(path, ignore=[orphan.key])
        elif picked != "later":
            decisions.update(path, match={orphan.key: picked[2]})
        if picked != "later":
            history.append(i)
        i += 1
    return len(history)
