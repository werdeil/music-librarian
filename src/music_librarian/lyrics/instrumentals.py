"""The tracks the dashboard's review answered as instrumental, kept in instrumentals.txt in the
state folder (one path relative to the library root per line) until a pass writes the
INSTRUMENTAL mark as their lyrics through tag, journaled like any edit.
"""
import os
from dataclasses import replace
from pathlib import Path

from music_librarian import tag
from music_librarian.lyrics import INSTRUMENTAL
from music_librarian.media import fields

FILE = "instrumentals.txt"


def load(state_dir: Path):
    """The tracks answered as instrumental, relative to the root, in the order answered."""
    try:
        text = (Path(state_dir) / FILE).read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    return [line for line in text.splitlines() if line.strip()]


def save(state_dir: Path, rels):
    path = Path(state_dir) / FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text("".join(f"{rel}\n" for rel in rels), encoding="utf-8")
    os.replace(temp, path)


def add(state_dir: Path, rel):
    answered = load(state_dir)
    if rel not in answered:
        save(state_dir, [*answered, rel])


def remove(state_dir: Path, rel):
    save(state_dir, [r for r in load(state_dir) if r != rel])


def waiting(root: Path, rel):
    """Whether the answer for `rel` still has something to write: the track is there, without lyrics."""
    path = Path(root) / rel
    try:
        return path.is_file() and fields.lyrics(path) == []
    except Exception:
        return False


def run(opts: tag.Options, log):
    """A tag pass writing the mark on the tracks answered as instrumental (`opts.edits` ignored);
    applied, the answers written or no longer waiting are dropped from the file."""
    answered = load(opts.state_dir)
    edits = []
    for rel in answered:
        if waiting(opts.root, rel):
            edits.append(tag.Edit(opts.root / rel, fields.LYRICS, INSTRUMENTAL, []))
        else:
            log(f"SKIPPED  (gone, or has lyrics since)  {rel}")
    code = tag.run(replace(opts, edits=edits), log)
    if opts.apply:
        save(opts.state_dir, [r for r in load(opts.state_dir) if r not in answered or waiting(opts.root, r)])
    return code
