"""The tracks whose lyrics no provider carries yet, kept in lyrics-awaited.txt in the state folder.

A line names a track by what its lyrics are looked up with: ARTIST — TITLE — ALBUM — M:SS. Names
are compared without case or accents, an em dash in one as a hyphen, durations within
VERIFY_DURATION_TOLERANCE: the mark follows a track through moves and is lost when one of those changes.
"""
import os
import re
import unicodedata
from pathlib import Path

from music_librarian.lyrics.matching import VERIFY_DURATION_TOLERANCE, float_duration

FILE = "lyrics-awaited.txt"
SEPARATOR = "—"
HEADER = """\
# Tracks whose lyrics are not available yet, still looked up by every enrich, one per line:
# ARTIST — TITLE — ALBUM — M:SS (album and duration may be left empty)
"""
DURATION = re.compile(r"^(\d+):([0-5]\d)$")


def _fold(text):
    text = unicodedata.normalize("NFKD", (text or "").replace(SEPARATOR, "-").casefold())
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).split())


def _key(meta):
    return tuple(_fold(meta.get(name)) for name in ("artist", "title", "album"))


def _seconds(text):
    m = DURATION.match(text.strip())
    return int(m[1]) * 60 + int(m[2]) if m else None


def line(meta):
    """The line naming the track of `meta` ({artist, title, album, duration})."""
    seconds = float_duration(meta.get("duration"))
    duration = "" if seconds is None else f"{int(seconds) // 60}:{int(seconds) % 60:02d}"
    names = [(meta.get(name) or "").replace(SEPARATOR, "-") for name in ("artist", "title", "album")]
    return f" {SEPARATOR} ".join([*names, duration]).strip()


def _parse(text):
    """(key, seconds or None) of a line, None for a comment or a line that cannot be read."""
    if not text.strip() or text.lstrip().startswith("#"):
        return None
    parts = [p.strip() for p in text.split(SEPARATOR)]
    if not 2 <= len(parts) <= 4:
        return None
    artist, title, album, duration = parts + [""] * (4 - len(parts))
    return (_fold(artist), _fold(title), _fold(album)), _seconds(duration)


def _close(seconds, other):
    return seconds is None or other is None or abs(seconds - other) <= VERIFY_DURATION_TOLERANCE


class Awaited:
    """The tracks of the file, read once."""

    def __init__(self, state_dir: Path):
        self.marked = {}
        for text in _lines(Path(state_dir) / FILE):
            parsed = _parse(text)
            if parsed:
                self.marked.setdefault(parsed[0], []).append(parsed[1])

    def holds(self, meta):
        """Whether the track of `meta` is awaited."""
        if not meta:
            return False
        seconds = float_duration(meta.get("duration"))
        return any(_close(seconds, other) for other in self.marked.get(_key(meta), ()))


def _lines(path: Path):
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []


def mark(state_dir: Path, metas):
    """Add the tracks of `metas` not awaited yet to the file."""
    held = Awaited(state_dir)
    new = [m for m in metas if m and not held.holds(m)]
    if new:
        _write(Path(state_dir) / FILE, (_lines(Path(state_dir) / FILE) or HEADER.splitlines())
               + [line(m) for m in new])


def unmark(state_dir: Path, metas):
    """Remove from the file every line naming one of the tracks of `metas`."""
    path = Path(state_dir) / FILE
    lines = _lines(path)
    metas = [m for m in metas if m]
    kept = [text for text in lines if not _names(_parse(text), metas)]
    if len(kept) != len(lines):
        _write(path, kept)


def _names(parsed, metas):
    if parsed is None:
        return False
    key, seconds = parsed
    return any(_key(m) == key and _close(float_duration(m.get("duration")), seconds) for m in metas)


def _write(path: Path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, path)
