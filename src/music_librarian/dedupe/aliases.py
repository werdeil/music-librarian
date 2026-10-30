"""The aliases file: the spelling each name is rewritten to, and the names that differ."""
import os
import re
from pathlib import Path

from music_librarian.split.rules import key

FILE = "artist-aliases.txt"
HEADER = """\
# Names of one artist spelled several ways, one per line:
#   VARIANT => NAME    the variant is rewritten NAME in the tags
#   NAME != OTHER      two different artists, never proposed again
"""
LINE = re.compile(r"^(.*?)\s*(=>|!=)\s*(.*)$")
MAX_CHAIN = 10


def parse(line):
    """(variant, "=>" or "!=", other) for an entry line, None for a comment or blank line."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    m = LINE.match(line)
    return (m[1], m[2], m[3].strip()) if m and m[1] and m[3].strip() else ()


def identity(entry):
    variant, kind, other = entry
    return (kind, key(variant)) if kind == "=>" else (kind, frozenset((key(variant), key(other))))


def load(path: Path, log):
    """({key(variant): name}, {frozenset({key(a), key(b)})} of different names)."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, set()
    aliases, different = {}, set()
    for n, line in enumerate(text.splitlines(), 1):
        entry = parse(line)
        if entry == ():
            log(f"ERROR    {path}:{n}: expected 'VARIANT => NAME' or 'NAME != OTHER', line ignored")
        elif entry and entry[1] == "=>":
            aliases[key(entry[0])] = entry[2]
        elif entry:
            different.add(identity(entry)[1])
    return aliases, different


def entries(path: Path):
    """The file's (variant, "=>" or "!=", other) entries as written, in order; unreadable lines
    left out."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    return [entry for line in text.splitlines() if (entry := parse(line))]


def canonical(name, aliases):
    for _ in range(MAX_CHAIN):
        target = aliases.get(key(name))
        if target is None or target == name:
            break
        name = target
    return name


def update(path: Path, add=(), drop=()):
    """Write the entries `add` (replacing those with the same identity) and remove the
    entries whose identity is in `drop`."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = HEADER.splitlines()
    gone = set(drop) | {identity(e) for e in add}
    lines = [line for line in lines if not parse(line) or identity(parse(line)) not in gone]
    lines += [f"{a} {kind} {b}" for a, kind, b in add]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, path)
