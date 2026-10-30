"""Library files: what counts as audio, as junk, and emptied folders."""
from pathlib import Path

AUDIO_EXT = {".m4a", ".mp3", ".flac"}
JUNK_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}


def is_junk(p: Path):
    return p.name in JUNK_NAMES or p.name.startswith("._")


def prune_empty(d: Path, stops):
    """Remove d and its parents while they are empty, never reaching the
    folders in `stops` (root, folders passed as arguments)."""
    n = 0
    while d not in stops and any(d.is_relative_to(s) for s in stops):
        try:
            d.rmdir()
        except OSError:
            break
        n += 1
        d = d.parent
    return n


def relative(path, root):
    """`path` as the log shows it: relative to `root` when under it, else whole."""
    try:
        return str(Path(path).relative_to(root))
    except ValueError:
        return str(path)
