"""Which credits hold several names, and what to write instead."""
import re
import unicodedata

SEPARATOR = re.compile(r"\s*[,/]\s*")
LYRION_SEPARATOR = "; "
KEEP, SPLIT, PENDING = "keep", "split", "?"


def key(name):
    return " ".join(unicodedata.normalize("NFC", name).casefold().split())


def names_of(value):
    """The names of a tag value, split the way Lyrion splits it."""
    return [c.strip() for c in value.split(";") if c.strip()]


def fragments(credit):
    return [f for f in SEPARATOR.split(credit.strip()) if f]


def known_names(albumartists, others):
    """Names seen on their own in the library: every album artist (a band name may
    hold a comma or a slash), and the artists and composers that hold neither."""
    names = {key(c) for v in albumartists for c in names_of(v)}
    names |= {key(c) for v in others for c in names_of(v) if len(fragments(c)) < 2}
    return names


def resolve(credit, known, decisions):
    """(names, status) for one credit, status being KEEP, SPLIT or PENDING.

    A decision from the file wins; otherwise a known name is kept, and a credit
    is split only when every fragment is a known name."""
    parts = fragments(credit)
    if len(parts) < 2:
        return [credit], KEEP
    decision = decisions.get(key(credit))
    if decision == KEEP or (decision is None and key(credit) in known):
        return [credit], KEEP
    if decision == SPLIT or (decision is None and all(key(p) in known for p in parts)):
        return parts, SPLIT
    if decision in (None, PENDING):
        return [credit], PENDING
    return names_of(decision), SPLIT


def rewrite(value, known, decisions):
    """(new value, or None when it stays as is; credits awaiting a decision)."""
    names, pending, changed = [], [], False
    for credit in names_of(value):
        parts, status = resolve(credit, known, decisions)
        if status == PENDING:
            pending.append(credit)
        changed |= status == SPLIT
        names += parts
    if not changed:
        return None, pending
    unique = {}
    for name in names:
        unique.setdefault(key(name), name)
    new = LYRION_SEPARATOR.join(unique.values())
    return (new if new != value else None), pending


def decided_names(decisions):
    """Names the decisions vouch for: a kept credit, and the names a split one gives."""
    names = set()
    for credit, decision in decisions.items():
        if decision == KEEP:
            names.add(key(credit))
        elif decision == SPLIT:
            names |= {key(f) for f in fragments(credit)}
        elif decision != PENDING:
            names |= {key(n) for n in names_of(decision)}
    return names


def suggest(credit, known):
    """SPLIT or KEEP, the likelier answer for a credit awaiting a decision."""
    parts = fragments(credit)
    if any(key(p) in known for p in parts):
        return SPLIT
    if "," not in credit:
        return KEEP if all(len(p) <= 3 for p in parts) else SPLIT
    if "/" not in credit and all(" " not in p for p in parts[:-1]):
        return KEEP
    return SPLIT
