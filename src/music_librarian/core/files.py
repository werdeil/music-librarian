"""Library files: what counts as audio, as junk, emptied folders, and the hidden folders of the Inbox
left behind."""
import shutil
import time
from pathlib import Path

AUDIO_EXT = {".m4a", ".mp3", ".flac"}
JUNK_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}
ABANDONED_HOURS = 24


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


def last_change(folder: Path):
    """The latest mtime of what `folder` holds, a file moved away meanwhile left out; the folder's own
    when it holds nothing."""
    found = []
    for path in folder.rglob("*"):
        try:
            found.append(path.stat().st_mtime)
        except FileNotFoundError:
            pass
    return max(found, default=folder.stat().st_mtime)


def drop_abandoned(inbox: Path, prefix):
    """Remove the folders of the Inbox named `prefix`… that nothing was written to for ABANDONED_HOURS:
    a batch or a download its process left unfinished."""
    for folder in inbox.glob(prefix + "*"):
        try:
            abandoned = folder.is_dir() and last_change(folder) < time.time() - ABANDONED_HOURS * 3600
        except FileNotFoundError:
            continue
        if abandoned:
            shutil.rmtree(folder, ignore_errors=True)
