"""Align the genre of every music file with the validated genre list.

The list is a text file, one validated genre per line, optionally followed by
the names that stand for it:

    Rock
    Rap / Hip-hop / RnB: hip hop, hip-hop, rap, r&b, trap
    # a comment

Names are compared without case, accents, spaces or punctuation, so "Hip Hop",
"hip-hop" and "HIPHOP" are one name. A tag holding several genres ("Rock; Pop",
"Electronica/Dance") takes the first one the list knows. A genre the list does
not know is left as it is and reported, so it is picked up on a later pass once
the list maps it.

With GENRE_LOOKUP=itunes, an album with tracks the list cannot align is searched
on the iTunes Search API (store ITUNES_COUNTRY, us by default): when the list
knows Apple's genre for it, those tracks take it. Answers are kept in
genre_lookup.json in the state folder; albums not found are searched again after
30 days. Each re-tagged file keeps its modification time, one second
later. The tags are read through the metadata cache (media/metadata_cache.py).
A stop request lets the current album finish, then stops (exit code 4);
a failed file or an unreadable list gives exit code 2.

Usage:
    python -m music_librarian.enrich.genres /path/to/music [--dry-run] [--genres FILE]

The list is --genres, else $MUSIC_LIBRARIAN_GENRES, else genres.txt in the state
folder. Without a list nothing is done. Targets are as for enrich.covers.
"""
import argparse
import json
import os
import re
import sys
import time
import unicodedata
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from music_librarian.core.files import AUDIO_EXT
from music_librarian.core.lock import STOP, handle_stop_signals
from music_librarian.core.log import Log, count
from music_librarian.core.state import state_path
from music_librarian.enrich.common import album_folders, finish, label
from music_librarian.integrations import itunes
from music_librarian.lyrics.matching import fold_name
from music_librarian.media import metadata_cache, tags
from music_librarian.media.metadata_cache import MetadataCache

SEPARATORS = re.compile(r"[;,/|]")
LOOKUP_CACHE = "genre_lookup.json"
RETRY_MISS = 30 * 24 * 3600  # an album iTunes did not have is searched again after this


class GenreListError(Exception):
    """The genre list cannot be read or contradicts itself."""


def key(name):
    """The name as it is compared: no case, accents, spaces or punctuation."""
    decomposed = unicodedata.normalize("NFKD", name.casefold())
    return "".join(c for c in decomposed if c.isalnum())


def default_list():
    return list_path(state_path())


def list_path(state_dir: Path):
    """The validated list: $MUSIC_LIBRARIAN_GENRES, else genres.txt in the state folder."""
    return Path(os.environ.get("MUSIC_LIBRARIAN_GENRES") or Path(state_dir) / "genres.txt")


def _lines(path):
    try:
        return Path(path).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []


def _line_of(lines, genre):
    return next((i for i, line in enumerate(lines) if line.strip() and not line.lstrip().startswith("#")
                 and key(line.partition(":")[0]) == key(genre)), None)


def _save(path, lines):
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _names(name):
    # the list's own separators; key() ignores punctuation, so a space stands for them
    return re.sub(r"[:,]", " ", name).strip()


def add_to_list(path, genre, alias=None):
    """Add `alias` to the line of `genre` in the list at `path`, or the line `genre` itself when
    the list has none."""
    lines = _lines(path)
    i = _line_of(lines, genre)
    alias = _names(alias) if alias else None
    if i is None:
        lines.append(f"{_names(genre)}: {alias}" if alias else _names(genre))
    elif alias:
        name, _, aliases = lines[i].partition(":")
        names = [a.strip() for a in aliases.split(",") if a.strip()] + [alias]
        lines[i] = f"{name.strip()}: {', '.join(names)}"
    _save(path, lines)


def remove_from_list(path, genre, alias=None):
    """Take back add_to_list(path, genre, alias)."""
    lines = _lines(path)
    i = _line_of(lines, genre)
    if i is None:
        return
    if alias is None:
        del lines[i]
    else:
        name, _, aliases = lines[i].partition(":")
        kept = [a.strip() for a in aliases.split(",") if a.strip() and key(a) != key(alias)]
        lines[i] = f"{name.strip()}: {', '.join(kept)}" if kept else name.strip()
    _save(path, lines)


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


class Lookup:
    """Album genres from iTunes, remembered in the state folder between passes."""

    def __init__(self, client, cache_path, log, save=True):
        self.client, self.path, self.log, self.save = client, Path(cache_path), log, save
        self.offline = False
        try:
            self.cache = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.cache = {}

    def genre(self, artist, album):
        """Apple's genre for the album, or None when not found or iTunes is unreachable."""
        k = f"{fold_name(artist)}\t{fold_name(album)}"
        hit = self.cache.get(k)
        if hit and (hit["genre"] or time.time() - hit["at"] < RETRY_MISS):
            return hit["genre"]
        if self.offline:
            return None
        try:
            genre = self.client.album_genre(artist, album)
        except itunes.Unavailable as exc:
            self.offline = True
            self.log(f"OFFLINE  iTunes: {exc}, no more lookups this pass")
            return None
        self.cache[k] = {"genre": genre, "at": time.time()}
        if self.save:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.cache, ensure_ascii=False, indent=0), encoding="utf-8")
        return genre


def retag(path, genre):
    mtime = os.stat(path).st_mtime
    tags.write_genre(path, genre)
    os.utime(path, (os.stat(path).st_atime, mtime + 1))


def album_of(snap):
    """(album artist, album) of a metadata snapshot, or None when either is missing."""
    t = {k.lower(): v for k, v in (snap or {}).get("tags", {}).items() if v}
    artist = (t.get("albumartist") or t.get("album artist") or t.get("artist") or [None])[0]
    album = (t.get("album") or [None])[0]
    return (artist, album) if artist and album else None


def process(folder, files, ctx):
    """Align the tracks of one album, logging one line per distinct change or unknown genre.

    Tracks the list cannot align take the album's iTunes genre when the list knows it.
    """
    rel, changes, left, first = label(folder), Counter(), {}, None
    for path in files:
        snap = ctx.cache.snapshot(path)
        values = metadata_cache.genres(snap)
        genre = resolve(values, ctx.mapping)
        if genre is None:
            left[path] = "; ".join(values)
            first = first or snap
        elif values != [genre]:
            changes[(ctx.write(path, genre), "; ".join(values), genre, "")] += 1

    album = album_of(first) if left and ctx.lookup else None
    found = ctx.lookup.genre(*album) if album else None
    mapped = resolve([found], ctx.mapping) if found else None
    unknown, missing = Counter(), 0
    for path, shown in left.items():
        if mapped:
            changes[(ctx.write(path, mapped), shown, mapped, found)] += 1
        elif shown:
            unknown[shown] += 1
        else:
            missing += 1

    hint = f", iTunes \"{found}\"" if found and not mapped else ""
    for (ok, old, new, source), n in changes.items():
        if not ok:
            continue
        tag = "WOULD" if ctx.dry_run else "ITUNES" if source else "GENRE"
        via = f"iTunes \"{source}\", " if source else ""
        was = f"\"{old}\"" if old else "(no genre)"
        ctx.log(f"{tag:<8} {rel}  {was} → \"{new}\" ({via}{count(n, 'track')})")
        ctx.counts["re-tagged"] += n
        ctx.counts["from iTunes"] += n if source else 0
    for old, n in unknown.items():
        ctx.log(f"UNKNOWN  {rel}  \"{old}\" ({count(n, 'track')}{hint})")
    if missing:
        ctx.log(f"NOGENRE  {rel}  ({count(missing, 'track')}{hint})")
    ctx.counts["unknown genre"] += sum(unknown.values())
    ctx.counts["without genre"] += missing


@dataclass
class Context:
    mapping: dict
    dry_run: bool
    log: Callable[[str], None]
    cache: MetadataCache | None = None
    lookup: Lookup | None = None
    counts: Counter = field(default_factory=lambda: Counter(
        {"re-tagged": 0, "from iTunes": 0, "unknown genre": 0, "without genre": 0, "failed": 0}))

    def write(self, path, genre):
        """True once `path` carries `genre` (or would, in a dry run)."""
        if self.dry_run:
            return True
        try:
            retag(path, genre)
            return True
        except (tags.GenreTagError, OSError) as exc:
            self.counts["failed"] += 1
            self.log(f"ERROR    {path}: {exc}")
            return False


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
        ctx = Context(load(path), args.dry_run, log)
    except GenreListError as exc:
        log(f"ERROR    {exc}")
        return 2
    client = itunes.from_env()
    if client:
        ctx.lookup = Lookup(client, state_path() / LOOKUP_CACHE, log, save=not args.dry_run)

    stopped = False
    with MetadataCache(state_path()) as ctx.cache:
        for folder, files in album_folders(args.targets, log):
            stopped = STOP.is_set()
            if stopped:
                log("STOP     requested: the albums left are aligned on the next pass")
                break
            process(folder, [p for p in files if os.path.splitext(p)[1].lower() in AUDIO_EXT], ctx)
    if not client:
        del ctx.counts["from iTunes"]
    return finish("Genres", dict(ctx.counts), stopped, log)


if __name__ == "__main__":
    handle_stop_signals()
    sys.exit(main())
