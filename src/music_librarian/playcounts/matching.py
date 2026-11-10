"""How much an orphan entry of Lyrion looks like a file of the library, both read from their
paths, which the library's filing gives the same shape: artist/album/NN title.

The note is out of 100: the title for 70, the artist folder for 20, the album for 10, plus 5 when
both have play counts within 20 % of each other.
"""
import re
import unicodedata
from dataclasses import dataclass
from pathlib import PurePosixPath

from rapidfuzz import fuzz

WEIGHTS = (("title", 0.70), ("artist", 0.20), ("album", 0.10))
PLAYS_BONUS, PLAYS_TOLERANCE = 5.0, 0.20
SURE, LIKELY, POSSIBLE = 90, 80, 60
_NUMBER = re.compile(r"^(?:\d+-)?\d+(?:\s*[-–—.])?\s+")
_NONALNUM = re.compile(r"[\W_]+")


def fold(text):
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return " ".join(_NONALNUM.sub(" ", text).split())


@dataclass(frozen=True)
class Named:
    """What a path says of a track, folded for comparison."""
    artist: str
    album: str
    title: str


def named(path):
    """Named for a path ending in artist/album/file; a shorter path leaves the folders empty."""
    path = PurePosixPath(path)
    folders = path.parts[-3:-1] if len(path.parts) >= 3 else ("", *path.parts[-2:-1])
    title = _NUMBER.sub("", path.stem) or path.stem
    return Named(fold(folders[0]), fold(folders[-1]), fold(title))


def close_counts(a, b):
    return bool(a and b) and min(a, b) / max(a, b) >= 1 - PLAYS_TOLERANCE


class Matcher:
    """Notes one orphan against `candidates`, [(key, path relative to the library, plays)]."""

    def __init__(self, candidates):
        self.candidates = [(key, rel, plays, named(rel), fold(rel)) for key, rel, plays in candidates]

    def best(self, path, plays=0, top=5, words=""):
        """The `top` candidates for the orphan at `path`, best first, as (note, key, relative
        path, plays); with `words`, among those whose path holds each of them."""
        orphan, words = named(path), fold(words).split()
        memo = {field: {} for field, _ in WEIGHTS}
        found = []
        for key, rel, count, candidate, text in self.candidates:
            if not all(word in text for word in words):
                continue
            note = 0.0
            for field, weight in WEIGHTS:
                ours, theirs = getattr(orphan, field), getattr(candidate, field)
                if ours and theirs:
                    ratio = memo[field].get(theirs)
                    if ratio is None:
                        ratio = memo[field][theirs] = fuzz.ratio(ours, theirs)
                    note += ratio * weight
            if close_counts(plays, count):
                note = min(note + PLAYS_BONUS, 100.0)
            found.append((note, key, rel, count))
        found.sort(key=lambda match: (-match[0], match[2]))
        return found[:top]
