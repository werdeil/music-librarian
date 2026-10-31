"""What the enrichment tools share: the album folders they work on, their summary and exit code,
and the time a dry run estimates the pass applied would take."""
import glob
import os
import re

from music_librarian.core.lock import EXIT_STOPPED
from music_librarian.core.log import Log
from music_librarian.media import tags


def expand(patterns):
    """Expand the targets into an ordered, de-duplicated list of paths.

    Shell-style wildcards are expanded here too, so a quoted `A*` works, unless the
    pattern names an existing path (`Album [Live]` is a folder, not a character class).
    Returns (paths, missing), `missing` being the patterns that matched nothing.
    """
    paths, missing, seen = [], [], set()
    for pattern in patterns:
        if glob.has_magic(pattern) and not os.path.exists(pattern):
            matches = sorted(glob.glob(pattern))
            if not matches:
                missing.append(pattern)
                continue
        else:
            matches = [pattern]
        for match in matches:
            if match not in seen:
                seen.add(match)
                paths.append(match)
    return paths, missing


def _music(names):
    # macOS leaves AppleDouble stubs (._track.mp3) beside the real files, with the extension
    return sorted(n for n in names if tags.is_music_file(n) and not n.startswith("."))


def album_folders(patterns, log=None):
    """Yield (folder, [music files]) for every folder holding music under `patterns`.

    Folders are walked in order, hidden ones skipped; a music file given as a target
    is its own album. Patterns that match nothing are reported, not fatal.
    """
    log = log or Log()
    paths, missing = expand(patterns)
    for pattern in missing:
        log(f"WARNING  no match for {pattern}")
    seen = set()
    for path in paths:
        if os.path.isfile(path) and tags.is_music_file(path):
            yield os.path.dirname(path) or ".", [path]
            continue
        if not os.path.isdir(path):
            log(f"WARNING  not a folder nor a music file: {path}")
            continue
        for dirpath, dirnames, names in os.walk(path):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            music = _music(names)
            if music and dirpath not in seen:
                seen.add(dirpath)
                yield dirpath, [os.path.join(dirpath, n) for n in music]


ESTIMATE = "estimated time"
# the seconds after the rounded time are what enrich adds up over its tools
ESTIMATE_LINE = re.compile(rf"^\s*{ESTIMATE}: .*\((\d+) s\)$")


def rough(seconds):
    """A duration as read at a glance: `under a minute`, `~12 min`, `~2 h 10 min`."""
    minutes = round(seconds / 60)
    if minutes < 1:
        return "under a minute"
    if minutes < 60:
        return f"~{minutes} min"
    hours, minutes = divmod(minutes, 60)
    return f"~{hours} h {minutes} min" if minutes else f"~{hours} h"


def estimate(seconds):
    """The `estimated time` value of a summary: rough(seconds), then the seconds themselves."""
    return f"{rough(seconds)} ({round(seconds)} s)"


def label(folder):
    """The album's last two path components, so a disc subfolder still names its album."""
    return os.sep.join(folder.rstrip(os.sep).split(os.sep)[-2:])


def finish(title, counts, stopped, log):
    """Log the `key: value` summary, zeros left out, and return the exit code: 4 when stopped,
    2 on a failure."""
    log(f"\n--- {title} summary ---")
    for key, value in counts.items():
        if value:
            log(f"{key:>20}: {value}")
    if stopped:
        return EXIT_STOPPED
    return 2 if counts.get("failed") else 0
