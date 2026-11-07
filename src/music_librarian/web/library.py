"""The library as the dashboard browses and edits it.

The album list comes from the metadata cache, never from a walk of the library, and is built
in the background (Refreshed); an album's page reads its tracks' fields from the files
themselves, the values an edit is then checked against by tag (seen).
"""
import json
import os
import threading
import time
import unicodedata
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from music_librarian import repeats
from music_librarian.core.files import AUDIO_EXT, is_junk, relative
from music_librarian.lyrics import is_instrumental, misses
from music_librarian.media import fields, metadata_cache, tags
from music_librarian.media.metadata_cache import MetadataCache

ALBUM_FIELDS = ("albumartist", "album", "year", "genre", "disctotal", "compilation")
TRACK_FIELDS = ("disc", "track", "title", "artist", "composer")
PAGE = 60
PATIENCE = 1.0
REBUILD_EVERY = 60
REPORT_EVERY = 200

_building = threading.local()
_one_build = threading.Lock()


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
    without_lyrics: int = 0
    compilation: bool = False
    repeated: int = 0   # tracks with a field holding a value twice or several values
    year: str = ""
    changed: float = 0  # the folder's mtime: when a track was last added, removed or renamed in it


SORTS = {
    "artist": None,   # the index's own order
    "title": lambda a: (fold(a.title or a.folder), fold(a.artist), a.folder),
    "year": lambda a: (not a.year[:4].isdigit(), -int(a.year[:4]) if a.year[:4].isdigit() else 0,
                       fold(a.artist), fold(a.title)),
    "recent": lambda a: (-a.changed, a.folder),
}
ONLY = {"lyrics": lambda a: a.without_lyrics, "repeats": lambda a: a.repeated,
        "compilations": lambda a: a.compilation}


def browse(albums, sort, only):
    """The albums sorted by `sort` and kept by `only` (keys of SORTS and ONLY, None for neither),
    and how many each filter of ONLY keeps of them: (albums, {filter: count})."""
    counts = {name: sum(1 for a in albums if keep(a)) for name, keep in ONLY.items()}
    if only in ONLY:
        albums = [a for a in albums if ONLY[only](a)]
    if SORTS.get(sort):
        albums = sorted(albums, key=SORTS[sort])
    return albums, counts


def folder_changed(folder: Path):
    try:
        return folder.stat().st_mtime
    except OSError:
        return 0


def first(track, *keys):
    easy = {k.lower(): v for k, v in track.items()}
    return next((easy[k][0] for k in keys if easy.get(k)), "")


def cache_stamp(state_dir: Path):
    """What changes when the metadata cache does: the times of metadata.db and of its WAL, the
    first None while there is no cache. A reader creates an empty WAL or deletes it on closing,
    so an empty one counts as none."""
    out = []
    for name in (metadata_cache.FILE, metadata_cache.FILE + "-wal"):
        try:
            st = os.stat(state_dir / name)
        except OSError:
            out.append(None)
            continue
        out.append(st.st_mtime_ns if st.st_size or name == metadata_cache.FILE else None)
    return tuple(out)


def cached_files(root: Path, state_dir: Path):
    """(path, snapshot) of the readable files under `root` in the cache that are still there,
    none without a cache: the dashboard never creates one."""
    if not (state_dir / metadata_cache.FILE).exists():
        return []
    out = []
    with MetadataCache(state_dir) as cache:
        total = cache.count(root)
        for done, (path, snap) in enumerate(cache.cached(root), 1):
            if snap is not None and os.path.exists(path):
                out.append((Path(path), snap))
            if done % REPORT_EVERY == 0 or done == total:
                report(done, total)
    return out


def report(done, total):
    """Tell the Refreshed building in this thread, if any, that it is `done` steps of `total` into
    the part of its build under way (see part), the whole build outside any."""
    value = getattr(_building, "value", None)
    if value is not None and total:
        start, end = getattr(_building, "span", (0.0, 1.0))
        value.done = start + (end - start) * min(done / total, 1.0)


@contextmanager
def part(start, end):
    """The share of the build, from `start` to `end` of it (0 to 1), that the reports within count
    for; at its end, the build is at `end`."""
    outer = getattr(_building, "span", (0.0, 1.0))
    _building.span = (outer[0] + (outer[1] - outer[0]) * start, outer[0] + (outer[1] - outer[0]) * end)
    try:
        yield
    finally:
        _building.span = outer
    report(end, 1)


def changed(stamp, built):
    """Whether a value built at `built`, (its stamp, time.monotonic()) or None, is out of date at
    `stamp` (a cache_stamp, then what else the value depends on): the cache changed, but while a
    pass writes it (its WAL holds data) at most every REBUILD_EVERY seconds."""
    if built is None:
        return True
    if stamp == built[0]:
        return False
    writing = stamp[1:] == built[0][1:] and stamp[0][1] is not None
    return not writing or time.monotonic() - built[1] >= REBUILD_EVERY


class Refreshed:
    """The value `build()` returns for the current `stamp()` (a cache_stamp, then what else the
    value depends on), built again in a thread of its own when the stamp changes (see changed).
    get() returns None until a first build ends, then the last value built, stale or not; only
    the first build keeps a request waiting, at most PATIENCE seconds from its start. A build
    tells how far it is through report(), from its own thread; one build runs at a time."""

    def __init__(self, stamp, build):
        self.stamp_of, self.build = stamp, build
        self.built, self.value, self.building = None, None, None
        self.done = 0.0
        self.deadline = 0
        self.lock = threading.Lock()

    def start(self):
        """Start a build when the stamp changed and none runs, without waiting on it."""
        stamp = self.stamp_of()
        with self.lock:
            if self.building is None and changed(stamp, self.built):
                self.deadline = time.monotonic() + PATIENCE
                self.done = 0.0
                self.building = threading.Thread(target=self._run, args=(stamp,), name="web-build",
                                                 daemon=True)
                self.building.start()
            return self.building, self.deadline

    def get(self):
        building, deadline = self.start()
        if building is not None and self.value is None:
            building.join(max(deadline - time.monotonic(), 0))
        return self.value

    def ready(self):
        """Whether a build has ended and none runs, without starting one."""
        with self.lock:
            return self.value is not None and self.building is None

    def progress(self):
        """How far the running build is, from 0 to 1; 1 when none runs."""
        with self.lock:
            return 1.0 if self.building is None else self.done

    def _run(self, stamp):
        _building.value, _building.span = self, (0.0, 1.0)
        try:
            with _one_build:   # builds run side by side contend for the GIL, each far slower
                value = self.build()
        except Exception:
            with self.lock:
                self.built, self.building = (stamp, time.monotonic()), None
            raise
        with self.lock:
            self.built, self.value, self.building = (stamp, time.monotonic()), value, None


class Index:
    """The albums of the metadata cache, one per folder holding tracks, rebuilt when the cache
    changes. A file gone from the library since it was cached is left out."""

    def __init__(self, root: Path, state_dir: Path):
        self.root, self.state_dir = root, state_dir
        self.albums = Refreshed(lambda: (cache_stamp(state_dir),), self._build)

    def get(self):
        """The albums, [] without a cache, None until they are first built."""
        return self.albums.get()

    def _build(self):
        if cache_stamp(self.state_dir)[0] is None:
            return []
        folders, bare, repeated = defaultdict(list), defaultdict(int), defaultdict(int)
        with part(0, 0.5):
            files = cached_files(self.root, self.state_dir)
        for path, snap in files:
            folders[path.parent].append(snap["tags"])
            bare[path.parent] += not snap.get("lyrics")
            repeated[path.parent] += bool(repeats.findings(repeats.from_snapshot(snap)))
        albums = []
        for done, (folder, tracks) in enumerate(folders.items(), 1):
            if done % REPORT_EVERY == 0:
                report(0.5 + 0.5 * done / len(folders), 1)
            artist = next((a for t in tracks if (a := first(t, "albumartist", "album artist", "artist"))),
                          "")
            title = next((a for t in tracks if (a := first(t, "album"))), "")
            words = {artist, title, *(first(t, key) for t in tracks for key in ("artist", "title"))}
            rel = str(folder.relative_to(self.root))
            year = next((y[:4] for t in tracks if (y := first(t, "date", "year"))), "")
            albums.append(Album(rel, artist, title, len(tracks), fold(" ".join([rel, *words])), bare[folder],
                                all(first(t, "compilation") == "1" for t in tracks), repeated[folder],
                                year, folder_changed(folder)))
        return sorted(albums, key=lambda a: (fold(a.artist), fold(a.title), a.folder))

    def search(self, query):
        """The albums matching every word of `query`, None until they are first built."""
        albums, words = self.get(), fold(query).split()
        return None if albums is None else [a for a in albums if all(w in a.text for w in words)]


def album_folder(root: Path, rel):
    """The album folder `rel` of the library, or None when it is not one."""
    folder = (root / rel).resolve()
    return folder if folder.is_relative_to(root) and folder.is_dir() else None


def track_file(root: Path, rel, name):
    """The track `name` of the album folder `rel`, or None when it is not one."""
    folder = album_folder(root, rel)
    if folder is None or not name or "/" in name or name in (".", ".."):
        return None
    path = folder / name
    return path if path.is_file() and path.suffix.lower() in AUDIO_EXT and not is_junk(path) else None


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


def repeated_fields(tracks):
    """{file name: {field: whether its values differ, else they only repeat}} of the tracks'
    fields holding a value twice or several values (see repeats)."""
    return {name: {field: differ for field, _, _, differ in repeats.findings(values)}
            for name, values in tracks if values and repeats.findings(values)}


def album_repeats(repeated):
    """{field: whether its values differ on a track} of the fields repeated_fields() found on any
    track, a difference winning."""
    out = {}
    for flags in repeated.values():
        for field, differ in flags.items():
            out[field] = out.get(field, False) or differ
    return out


def with_repeats(albums):
    """The albums with tracks whose fields hold a value twice or several values, the most such
    tracks first."""
    return sorted((a for a in albums if a.repeated), key=lambda a: (-a.repeated, a.folder))


def several_discs(tracks):
    """Whether the album page shows the disc numbers: not for one disc numbered 1 or not at all."""
    return (any(values and values["disc"] not in ([], ["1"]) for _, values in tracks)
            or common(tracks)["disctotal"] not in ("", "1"))


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
        if form.get(f"t{i}.replaygain_delete") and values.get(fields.REPLAYGAIN):
            out.append([str(Path(folder_rel) / file), fields.REPLAYGAIN, "", values[fields.REPLAYGAIN]])
    return out


def preview_changes(preview, root: Path):
    """What an album's page shows of a tag.Preview: the changes, [(field, before, after, [file
    names])] one per distinct change, the folders the tracks move between, [(from, to)], relative
    to `root`, the number of files renamed in their folder, and the problem lines."""
    groups = defaultdict(list)
    for path, name, old, new in preview.writes:
        groups[(name, fields.brief(name, old), fields.brief(name, new))].append(path.name)
    moved = sorted({(relative(src.parent, root), relative(dst.parent, root))
                    for src, dst in preview.moves if src.parent != dst.parent})
    return dict(fields=[(*key, files) for key, files in groups.items()], folders=moved,
                renamed=sum(1 for src, dst in preview.moves if src.parent == dst.parent),
                problems=preview.problems)


def cover_file(folder: Path):
    """The album's folder.jpg, whatever the case of its name, or None."""
    return next((p for p in folder.iterdir() if p.name.lower() == "folder.jpg" and p.is_file()), None)


def cover_version(root: Path, rel):
    """What changes when the album's cover does, for its URL: the time of its folder.jpg, else
    of its folder; "" for a folder not in the library."""
    folder = album_folder(root, rel)
    if folder is None:
        return ""
    try:
        return str((cover_file(folder) or folder).stat().st_mtime_ns)
    except OSError:
        return ""


def cover(folder: Path):
    """(bytes, mime type) of the album's folder.jpg, else of the first embedded cover of its
    tracks; None without either."""
    jpg = cover_file(folder)
    if jpg is not None:
        return jpg.read_bytes(), "image/jpeg"
    for p in sorted(folder.iterdir()):
        if p.is_file() and p.suffix.lower() in AUDIO_EXT and not is_junk(p):
            data = tags.read_cover(p)
            if data:
                return data, "image/png" if data.startswith(b"\x89PNG") else "image/jpeg"
    return None


def lyrics_state(folder: Path, names):
    """{file name: "instrumental" when its lyrics are the INSTRUMENTAL mark, else whether it has some}."""
    out = {}
    for name in names:
        try:
            text = tags.read_lyrics(folder / name)
        except Exception:
            text = None
        out[name] = "instrumental" if is_instrumental(text) else bool(text)
    return out


def replaygain_state(folder: Path, names):
    """{file name: its fields.replaygain() values, [] when it has none or is unreadable}."""
    out = {}
    for name in names:
        try:
            out[name] = fields.replaygain(folder / name)
        except Exception:
            out[name] = []
    return out


def without_lyrics(albums, root: Path, state_dir: Path):
    """[(album, the time its tracks were last looked up in vain or None)] of the albums with
    tracks without lyrics, the most such tracks first."""
    missed = {folder: at for folder, _n, at in missed_albums(root, state_dir)}
    bare = [a for a in albums if a.without_lyrics]
    return [(a, missed.get(a.folder)) for a in sorted(bare, key=lambda a: (-a.without_lyrics, a.folder))]


def missed_albums(root: Path, state_dir: Path):
    """[(folder relative to the root, tracks looked up in vain, last lookup)] of the albums of
    the library, the most tracks first."""
    folders = defaultdict(list)
    for path, at in misses.recorded(state_dir).items():
        if path.is_relative_to(root) and path.exists():
            folders[str(path.parent.relative_to(root))].append(at)
    return sorted(((f, len(ats), max(ats)) for f, ats in folders.items()), key=lambda a: (-a[1], a[0]))
