"""The decisions file: what to do with each credit the library cannot settle."""
import os
import re
from pathlib import Path

from music_librarian.split.rules import KEEP, PENDING, SPLIT, key

FILE = "split-decisions.txt"
HEADER = """\
# Credits that may hold several names, one per line: CREDIT => DECISION
# DECISION is split, keep, or the names to write separated by ";". A credit marked ? waits for a decision.
"""
LINE = re.compile(r"^(.*?)\s*=>\s*(.*)$")


def load(path: Path, log):
    """{key(credit): decision}; a line that cannot be read is reported and ignored."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    out = {}
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = LINE.match(line)
        if not m or not m[1]:
            log(f"ERROR    {path}:{n}: expected 'CREDIT => DECISION', line ignored")
            continue
        decision = m[2].strip() or PENDING
        out[key(m[1])] = decision.lower() if decision.lower() in (KEEP, SPLIT) else decision
    return out


def append_pending(path: Path, pending):
    new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        if new:
            f.write(HEADER)
        for credit in pending:
            f.write(f"{credit} => {PENDING}\n")


def record(path: Path, credit, decision):
    """Write the decision in place of the credit's line, or at the end of the file; None
    removes the line."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = HEADER.splitlines()
    line = [] if decision is None else [f"{credit} => {decision}"]
    for i, old in enumerate(lines):
        m = LINE.match(old.strip())
        if m and not old.lstrip().startswith("#") and key(m[1]) == key(credit):
            lines[i:i + 1] = line
            break
    else:
        lines += line
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, path)
