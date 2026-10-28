"""Align the genre of every music file with the validated genre list.

The list is a text file, one validated genre per line, optionally followed by
the names that stand for it:

    Rock
    Rap / Hip-hop/ RnB: hip hop, hip-hop, rap, r&b, trap
    # a comment

Names are compared without case, accents, spaces or punctuation, so "Hip Hop",
"hip-hop" and "HIPHOP" are one name. A tag holding several genres ("Rock; Pop",
"Electronica/Dance") takes the first one the list knows. A genre the list does
not know is left as it is and reported, so it is picked up on a later pass once
the list maps it. Each re-tagged file keeps its modification time, one second
later. A stop request lets the current album finish, then stops (exit code 4);
a failed file or an unreadable list gives exit code 2.

Usage:
    python -m music_librarian.enrich.genres /path/to/music [--dry-run] [--genres FILE]

The list is --genres, else $MUSIC_LIBRARIAN_GENRES, else genres.txt in the state
folder. Without a list nothing is done. Targets are as for enrich.covers.
"""
import argparse
import os
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

from music_librarian.core.files import AUDIO_EXT
from music_librarian.core.lock import STOP, handle_stop_signals
from music_librarian.core.log import Log
from music_librarian.core.state import state_path
from music_librarian.enrich.common import album_folders, finish, label
from music_librarian.media import tags

SEPARATORS = re.compile(r"[;,/|]")


class GenreListError(Exception):
    """The genre list cannot be read or contradicts itself."""


def key(name):
    """The name as it is compared: no case, accents, spaces or punctuation."""
    decomposed = unicodedata.normalize("NFKD", name.casefold())
    return "".join(c for c in decomposed if c.isalnum())


def default_list():
    return Path(os.environ.get("MUSIC_LIBRARIAN_GENRES") or state_path() / "genres.txt")


def load(path):
    """{key: validated genre} from the list at `path`. Raises GenreListError."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise GenreListError(f"{path}: {exc}") from exc
    mapping = {}
    for number, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        genre, _, aliases = line.partition(":")
        genre = genre.strip()
        for name in (genre, *aliases.split(",")):
            k = key(name)
            if not k:
                continue
            if mapping.get(k, genre) != genre:
                raise GenreListError(f"{path}:{number}: \"{name.strip()}\" already stands for "
                                     f"\"{mapping[k]}\"")
            mapping[k] = genre
    return mapping


def resolve(values, mapping):
    """The validated genre for a tag's values, or None when the list knows none of them."""
    for value in values:
        for candidate in (value, *SEPARATORS.split(value)):
            genre = mapping.get(key(candidate))
            if genre:
                return genre
    return None


def retag(path, genre):
    mtime = os.stat(path).st_mtime
    tags.write_genre(path, genre)
    os.utime(path, (os.stat(path).st_atime, mtime + 1))


def process(folder, files, mapping, dry_run, counts, log):
    """Align the tracks of one album, logging one line per distinct change or unknown genre."""
    changes, unknown, missing = Counter(), Counter(), 0
    for path in files:
        values = tags.read_genres(path)
        if not values:
            missing += 1
            continue
        genre = resolve(values, mapping)
        shown = "; ".join(values)
        if genre is None:
            unknown[shown] += 1
        elif values != [genre]:
            try:
                if not dry_run:
                    retag(path, genre)
                changes[(shown, genre)] += 1
            except (tags.GenreTagError, OSError) as exc:
                counts["failed"] += 1
                log(f"ERROR    {path}: {exc}")
    rel = label(folder)
    for (old, new), n in changes.items():
        log(f"{'WOULD' if dry_run else 'GENRE':<8} {rel}  \"{old}\" → \"{new}\" ({n} track(s))")
    for old, n in unknown.items():
        log(f"UNKNOWN  {rel}  \"{old}\" ({n} track(s))")
    if missing:
        log(f"NOGENRE  {rel}  ({missing} track(s))")
    counts["re-tagged"] += sum(changes.values())
    counts["unknown genre"] += sum(unknown.values())
    counts["without genre"] += missing


def main(argv=None):
    parser = argparse.ArgumentParser(description="Align the genres with the validated genre list.")
    parser.add_argument("targets", nargs="+", help="Directories to process (wildcards accepted).")
    parser.add_argument("--dry-run", action="store_true", help="Report the changes, write nothing.")
    parser.add_argument("--genres", type=Path, default=None,
                        help="Validated genre list (default: $MUSIC_LIBRARIAN_GENRES, "
                             "else genres.txt in the state folder).")
    args = parser.parse_args(argv)
    log = Log()

    path = args.genres or default_list()
    if not path.exists():
        log(f"SKIPPED  no genre list at {path}")
        return 0
    try:
        mapping = load(path)
    except GenreListError as exc:
        log(f"ERROR    {exc}")
        return 2

    counts = Counter({"re-tagged": 0, "unknown genre": 0, "without genre": 0, "failed": 0})
    stopped = False
    for folder, files in album_folders(args.targets, log):
        stopped = STOP.is_set()
        if stopped:
            log("STOP     requested: the albums left are aligned on the next pass")
            break
        files = [p for p in files if os.path.splitext(p)[1].lower() in AUDIO_EXT]
        process(folder, files, mapping, args.dry_run, counts, log)
    return finish("Genres", dict(counts), args.dry_run, stopped, log)


if __name__ == "__main__":
    handle_stop_signals()
    sys.exit(main())
