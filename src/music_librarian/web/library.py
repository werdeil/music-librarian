"""The library as the dashboard browses and edits it.

The album list comes from the metadata cache, never from a walk of the library; an album's
page reads its tracks' fields from the files themselves, the values an edit is then checked
against by tag (seen).
"""
import json
import os
import threading
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from music_librarian.core.files import AUDIO_EXT, is_junk
from music_librarian.media import fields, metadata_cache, tags
from music_librarian.media.metadata_cache import MetadataCache

ALBUM_FIELDS = ("albumartist", "album", "year", "genre", "disctotal", "compilation")
TRACK_FIELDS = ("disc", "track", "title", "artist", "composer")
PAGE = 60


def fold(text):
    """The text as searched: no case, no accents."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold())
                   if not unicodedata.combining(c))


@dataclass
class Album:
    folder: str   # relative to the root
    artist: str
    title: str
    tracks: int
    text: str     # what a search matches, folded


def first(track, *keys):
    easy = {k.lower(): v for k, v in track.items()}
    return next((easy[k][0] for k in keys if easy.get(k)), "")


def cache_stamp(state_dir: Path):
    """What changes when the metadata cache does: the times of metadata.db and of its WAL, the
    first None while there is no cache."""
    out = []
    for name in (metadata_cache.FILE, metadata_cache.FILE + "-wal"):
        try:
            out.append(os.stat(state_dir / name).st_mtime_ns)
        except OSError:
            out.append(None)
    return tuple(out)


def cached_files(root: Path, state_dir: Path):
    """(path, snapshot) of the readable files under `root` in the cache that are still there."""
    with MetadataCache(state_dir) as cache:
        return [(Path(path), snap) for path, snap in cache.cached(root)
                if snap is not None and os.path.exists(path)]


class Index:
    """The albums of the metadata cache, one per folder holding tracks, rebuilt when the cache
    changes. A file gone from the library since it was cached is left out."""

    def __init__(self, root: Path, state_dir: Path):
        self.root, self.state_dir = root, state_dir
        self.stamp, self.albums = None, []
        self.lock = threading.Lock()

    def get(self):
        with self.lock:
            stamp = cache_stamp(self.state_dir)
            if stamp != self.stamp:
                self.albums = self._build() if stamp[0] is not None else []
                self.stamp = stamp
            return self.albums

    def _build(self):
        folders = defaultdict(list)
        for path, snap in cached_files(self.root, self.state_dir):
            folders[path.parent].append(snap["tags"])
        albums = []
        for folder, tracks in folders.items():
            artist = next((a for t in tracks if (a := first(t, "albumartist", "album artist", "artist"))),
                          "")
            title = next((a for t in tracks if (a := first(t, "album"))), "")
            words = {artist, title, *(first(t, key) for t in tracks for key in ("artist", "title"))}
            rel = str(folder.relative_to(self.root))
            albums.append(Album(rel, artist, title, len(tracks), fold(" ".join([rel, *words]))))
        return sorted(albums, key=lambda a: (fold(a.artist), fold(a.title), a.folder))

    def search(self, query):
        words = fold(query).split()
        return [a for a in self.get() if all(w in a.text for w in words)]


def album_folder(root: Path, rel):
    """The album folder `rel` of the library, or None when it is not one."""
    folder = (root / rel).resolve()
    return folder if folder.is_relative_to(root) and folder.is_dir() else None


def tracks_of(folder: Path):
    """[(file name, fields.read() values or None when unreadable)] of the folder's tracks."""
    out = []
    for p in sorted(folder.iterdir()):
        if p.is_file() and p.suffix.lower() in AUDIO_EXT and not is_junk(p):
            try:
                out.append((p.name, fields.read(p)))
            except Exception:
                out.append((p.name, None))
    return out


def joined(values):
    return "; ".join(values)


def common(tracks):
    """{album field: the value every readable track holds, or None when they differ}."""
    out = {}
    for name in ALBUM_FIELDS:
        seen = {joined(values[name]) for _, values in tracks if values}
        out[name] = seen.pop() if len(seen) == 1 else None
    return out


def seen_state(tracks):
    """What the album page shows, carried by its form and compared on submit."""
    return json.dumps({"album": common(tracks),
                       "tracks": [[name, values] for name, values in tracks if values]})


def same(name, value, shown):
    """True when `value` writes what `shown` holds: "0" is no compilation flag, "07" track 7."""
    try:
        return fields.check(name, [value]) == fields.check(name, [shown])
    except fields.FieldError:
        return value == shown


def edits(folder_rel, form, seen):
    """The edits an album form asks for, as tag's dashboard options: [[path, field, value,
    values seen]] for each field whose value differs from the one shown. An album field
    applies to every track; one left empty while its tracks differed (shown None) is left alone."""
    out = []
    tracks = seen["tracks"]
    for name in ALBUM_FIELDS:
        value, shown = form.get(f"album.{name}", "").strip(), seen["album"][name]
        if (not value and shown is None) or (shown is not None and same(name, value, shown)):
            continue
        out += [[str(Path(folder_rel) / file), name, value, values[name]] for file, values in tracks]
    for i, (file, values) in enumerate(tracks):
        for name in TRACK_FIELDS:
            value = form.get(f"t{i}.{name}")
            if value is not None and not same(name, value.strip(), joined(values[name])):
                out.append([str(Path(folder_rel) / file), name, value.strip(), values[name]])
    return out


def cover(folder: Path):
    """(bytes, mime type) of the album's folder.jpg, else of the first embedded cover of its
    tracks; None without either."""
    jpg = folder / "folder.jpg"
    if jpg.is_file():
        return jpg.read_bytes(), "image/jpeg"
    for p in sorted(folder.iterdir()):
        if p.is_file() and p.suffix.lower() in AUDIO_EXT and not is_junk(p):
            data = tags.read_cover(p)
            if data:
                return data, "image/png" if data.startswith(b"\x89PNG") else "image/jpeg"
    return None
