"""The library as the dashboard browses and edits it.

The album list comes from the metadata cache, never from a walk of the library, and is built
in the background (Refreshed); an album's page reads its tracks' fields from the files
themselves, the values an edit is then checked against by tag (seen).
"""
import json
import os
import threading
import time
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field as dataclass_field, replace
from pathlib import Path

from music_librarian import dates, repeats
from music_librarian.core.files import AUDIO_EXT, is_junk, relative
from music_librarian.lyrics import INSTRUMENTAL, awaited, is_instrumental
from music_librarian.media import fields, metadata_cache, tags
from music_librarian.media.metadata_cache import MetadataCache
from music_librarian.physical import marks
from music_librarian.web import query
from music_librarian.web.query import fold

ALBUM_FIELDS = ("albumartist", "album", "year", "genre", "disctotal", "compilation")
TRACK_FIELDS = ("disc", "track", "title", "artist", "composer", "grouping")
PAGE = 60
TRACK_PAGE = 100
PATIENCE = 1.0
REBUILD_EVERY = 60
REPORT_EVERY = 200
WARM_KEPT = 8   # albums read ahead that are taken as still in the system's cache

_building = threading.local()
_one_build = threading.Lock()


@dataclass
class Album:  # pylint: disable=too-many-instance-attributes  # one per column the lists show
    folder: str   # relative to the root
    artist: str
    title: str
    tracks: int
    without_lyrics: int = 0   # neither instrumental nor awaited
    compilation: bool = False
    repeated: int = 0   # tracks with a field holding a value twice or several values
    year: str = ""
    changed: float = 0  # the folder's mtime: when a track was last added, removed or renamed in it
    awaited: int = 0
    mixed: tuple = ()   # the album fields whose tracks differ
    dates: tuple = ()   # what is wrong with its tracks' years, as dates.problems() tells
    physical: bool = False   # every track holds the marker of physical


SORTS = {
    "artist": None,   # the index's own order
    "title": lambda a: (fold(a.title or a.folder), fold(a.artist), a.folder),
    "year": lambda a: (not a.year[:4].isdigit(), -int(a.year[:4]) if a.year[:4].isdigit() else 0,
                       fold(a.artist), fold(a.title)),
    "recent": lambda a: (-a.changed, a.folder),
}
VIEWS = ("albums", "tracks")


@dataclass
class Catalog:
    albums: list   # of Album, in the index's order
    tracks: list   # of query.Track, album by album in that order, then by disc and number
    bare: list = dataclass_field(default_factory=list)      # (Track, metadata) of those without lyrics
    awaited: dict = dataclass_field(default_factory=dict)   # {folder: names of its tracks awaited}
    started: float = 0.0   # time.monotonic() when its build started

    def album(self, folder):
        return next((a for a in self.albums if a.folder == folder), None)

    def unsorted(self, folder):
        """{file name: metadata} of the folder's tracks without lyrics not awaited."""
        held = self.awaited.get(folder, set())
        return {t.name: meta for t, meta in self.bare if t.folder == folder and t.name not in held}


def with_awaited(catalog, marked):
    """The catalog with its tracks without lyrics that `marked` (an awaited.Awaited) holds flagged
    awaited, and left out of their album's count of tracks without lyrics."""
    held = {id(t): t for t, meta in catalog.bare if marked.holds(meta)}
    names, flagged = defaultdict(set), {}
    for t in held.values():
        names[t.folder].add(t.name)
        flagged[id(t)] = query.Track(t.folder, t.name, t.values, t.tags, t.flags | {"awaited"}, t.duration)
    albums = [replace(a, without_lyrics=a.without_lyrics - len(names[a.folder]), awaited=len(names[a.folder]))
              if a.folder in names else a for a in catalog.albums]
    tracks = [flagged.get(id(t), t) for t in catalog.tracks] if flagged else catalog.tracks
    return Catalog(albums, tracks, catalog.bare, dict(names), catalog.started)


def with_reread(catalog, reread):
    """The catalog with the albums of `reread` ({folder: {Album field: value}}) holding those values."""
    if not reread:
        return catalog
    albums = [replace(a, **reread[a.folder]) if a.folder in reread else a for a in catalog.albums]
    return Catalog(albums, catalog.tracks, catalog.bare, catalog.awaited, catalog.started)


def with_settled(catalog, settled):
    """The catalog with the albums of `settled` counting no track without lyrics."""
    if not settled:
        return catalog
    albums = [replace(a, without_lyrics=0) if a.folder in settled else a for a in catalog.albums]
    return Catalog(albums, catalog.tracks, catalog.bare, catalog.awaited, catalog.started)


@dataclass
class Found:
    albums: list    # the albums holding a track kept, sorted
    tracks: list    # the tracks kept, in the albums' order
    matched: dict   # {folder: tracks kept in it}, empty for a library neither searched nor filtered
    counts: dict    # {preset or "all": the albums, or tracks in the track list, kept of those the words keep}


def find(catalog, words, search, sort, view):
    """What the library shows for the words of the search box, a query.Search, a key of SORTS and
    a view of VIEWS: a track is kept when its own text holds every word and the search keeps it,
    an album when it holds a track kept."""
    words = fold(words).split()
    searched = [t for t in catalog.tracks if all(w in t.text for w in words)] if words else catalog.tracks
    tracks = [t for t in searched if search.keeps(t)]
    filtered = bool(words or search.conditions)
    matched = Counter(t.folder for t in tracks) if filtered else {}
    albums = [a for a in catalog.albums if a.folder in matched] if filtered else list(catalog.albums)
    if SORTS.get(sort):
        albums = sorted(albums, key=SORTS[sort])
        order = {a.folder: i for i, a in enumerate(albums)}
        tracks = sorted(tracks, key=lambda t: order[t.folder])
    kept_tracks, kept_albums = Counter(), defaultdict(set)
    for t in searched:
        for name in t.presets:
            kept_tracks[name] += 1
            kept_albums[name].add(t.folder)
    counts = {name: kept_tracks[name] if view == "tracks" else len(kept_albums[name])
              for name in query.PRESETS}
    counts["all"] = len(searched) if view == "tracks" else len({t.folder for t in searched})
    return Found(albums, tracks, matched, counts)


def folder_changed(folder: Path):
    try:
        return folder.stat().st_mtime
    except OSError:
        return 0


def first(track, *keys):
    easy = {k.lower(): v for k, v in track.items()}
    return next((easy[k][0] for k in keys if easy.get(k)), "")


def album_values(track):
    """{album field: its value} of a track's cached tags, as the album's page compares them."""
    total = first(track, "disctotal", "totaldiscs") or first(track, "discnumber").partition("/")[2]
    return {"albumartist": first(track, "albumartist", "album artist"), "album": first(track, "album"),
            "year": first(track, "date", "year"), "genre": first(track, "genre"),
            "disctotal": total, "compilation": "1" if first(track, "compilation") == "1" else ""}


def as_tags(values):
    """A track's fields.read_all() values as the cached tags that album_values() reads."""
    return {"albumartist": values["albumartist"], "album": values["album"], "date": values["year"],
            "genre": values["genre"], "discnumber": values["disc"], "disctotal": values["disctotal"],
            "compilation": values["compilation"]}


def reread_album(tracks):
    """{Album field: value} of the album fields that tracks_of() `tracks`, read from the files, give
    as the album list builds them from the cache: mixed, dates."""
    held = [as_tags(values) for _, values in tracks if values]
    return {"mixed": mixed_fields(held), "dates": dates.problems([first(t, "date", "year") for t in held])}


def mixed_fields(held):
    """The album fields whose values differ between the tracks' cached tags `held`, in
    ALBUM_FIELDS' order; the genre counts only when no track is flagged compilation."""
    values = [album_values(t) for t in held]
    compilation = any(v["compilation"] for v in values)
    return tuple(name for name in ALBUM_FIELDS if len({v[name] for v in values}) > 1
                 and not (name == "genre" and compilation))


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

    def awaited(self):
        """get(), once the build that runs was waited for, at most PATIENCE seconds from its
        start: the last value built is only returned stale past that."""
        building, deadline = self.start()
        if building is not None:
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
    """The albums of the metadata cache, one per folder holding tracks, and their tracks, rebuilt
    when the cache changes. A file gone from the library since it was cached is left out. The
    awaited tracks are laid over the last build whenever their file changes, without a rebuild, and
    so are the albums whose page found no track left to sort out (see settle), and the album fields
    their page found differing and the problems it found with their years (see reread)."""

    def __init__(self, root: Path, state_dir: Path):
        self.root, self.state_dir = root, state_dir
        self.albums = Refreshed(lambda: (cache_stamp(state_dir),), self._build)
        self.marked, self.lock = None, threading.Lock()   # (build, what is laid over, Catalog laid over)
        self.settled = {}   # {folder: time.monotonic() its page found nothing to sort out}
        self.reread = {}   # {folder: (time.monotonic() its page read its files, {Album field: value})}

    def settle(self, rel, settled):
        """Note whether the page of the album `rel`, read from its files, found no track left to sort
        out: until a build started after the note, the album then counts none without lyrics."""
        with self.lock:
            if settled:
                self.settled[rel] = time.monotonic()
            else:
                self.settled.pop(rel, None)

    def note(self, rel, found):
        """Note what the page of the album `rel` found reading its files, {Album field: value} (mixed,
        dates): until a build started after the note, the album holds those values."""
        with self.lock:
            self.reread[rel] = time.monotonic(), found

    def get(self):
        """The albums, [] without a cache, None until they are first built."""
        catalog = self.catalog()
        return None if catalog is None else catalog.albums

    def catalog(self):
        """The Catalog with the awaited tracks, None until it is first built."""
        built = self.albums.get()
        if built is None:
            return None
        stamp = awaited_stamp(self.state_dir)
        with self.lock:
            self.settled = {rel: at for rel, at in self.settled.items() if at > built.started}
            self.reread = {rel: seen for rel, seen in self.reread.items() if seen[0] > built.started}
            reread = {rel: found for rel, (_, found) in self.reread.items()}
            over = stamp, frozenset(self.settled), tuple(sorted((rel, tuple(sorted(found.items())))
                                                                for rel, found in reread.items()))
            if self.marked is None or self.marked[0] is not built or self.marked[1] != over:
                laid = with_awaited(built, awaited.Awaited(self.state_dir))
                self.marked = built, over, with_reread(with_settled(laid, over[1]), reread)
            return self.marked[2]

    def _build(self):
        started = time.monotonic()
        if cache_stamp(self.state_dir)[0] is None:
            return Catalog([], [], started=started)
        folders, bare, repeated = defaultdict(list), defaultdict(int), defaultdict(int)
        physical = defaultdict(int)
        tracks, rels, without = [], {}, []
        with part(0, 0.5):
            files = cached_files(self.root, self.state_dir)
        for path, snap in files:
            folder = path.parent
            rel = rels.setdefault(folder, str(folder.relative_to(self.root)))
            folders[folder].append(snap["tags"])
            bare[folder] += not snap.get("lyrics")
            twice = bool(repeats.findings(repeats.from_snapshot(snap)))
            repeated[folder] += twice
            owned = marks.marked(snap["tags"].get("comment", []))
            physical[folder] += owned
            flags = [name for name, held in (("lyrics", snap.get("lyrics")), ("cover", snap.get("cover")),
                                              ("replaygain", snap.get("replaygain")), ("repeated", twice),
                                              ("compilation", first(snap["tags"], "compilation") == "1"),
                                              ("physical", owned))
                     if held]
            tracks.append(query.track_of(rel, path.name, snap, flags))
            if not snap.get("lyrics"):
                without.append((tracks[-1], metadata_cache.metadata(snap)))
        albums = []
        for done, (folder, held) in enumerate(folders.items(), 1):
            if done % REPORT_EVERY == 0:
                report(0.5 + 0.5 * done / len(folders), 1)
            artist = next((a for t in held if (a := first(t, "albumartist", "album artist", "artist"))), "")
            title = next((a for t in held if (a := first(t, "album"))), "")
            year = next((y[:4] for t in held if (y := first(t, "date", "year"))), "")
            albums.append(Album(rels[folder], artist, title, len(held), bare[folder],
                                all(first(t, "compilation") == "1" for t in held), repeated[folder],
                                year, folder_changed(folder), mixed=mixed_fields(held),
                                dates=dates.problems([first(t, "date", "year") for t in held]),
                                physical=physical[folder] == len(held)))
        albums.sort(key=lambda a: (fold(a.artist), fold(a.title), a.folder))
        order = {a.folder: i for i, a in enumerate(albums)}
        tracks.sort(key=lambda t: (order[t.folder], query.number_of(t, "disc") or 0,
                                   query.number_of(t, "track") or 0, t.name))
        return Catalog(albums, tracks, without, started=started)


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


class Warmer:
    """Reads the tracks of the albums a page is likely to open next, in order, in a thread of its
    own, so those pages read them from the system's cache instead of the disk; albums asked for
    while another is read replace those still waiting, and one read lately is not read again."""

    def __init__(self):
        self.wanted, self.reading, self.read = [], False, []
        self.lock = threading.Lock()

    def ask(self, *folders: Path):
        with self.lock:
            self.wanted = [f for f in folders if f not in self.read]
            if self.reading or not self.wanted:
                return
            self.reading = True
        threading.Thread(target=self._run, name="web-warm", daemon=True).start()

    def _run(self):
        while True:
            with self.lock:
                if not self.wanted:
                    self.reading = False
                    return
                folder = self.wanted.pop(0)
                self.read = [folder, *self.read][:WARM_KEPT]
            try:
                tracks_of(folder)
            except OSError:
                pass


WARMER = Warmer()


def tracks_of(folder: Path):
    """[(file name, fields.read_all() values or None when unreadable)] of the folder's tracks."""
    out = []
    for p in sorted(folder.iterdir()):
        if p.is_file() and p.suffix.lower() in AUDIO_EXT and not is_junk(p):
            try:
                out.append((p.name, fields.read_all(p)))
            except Exception:
                out.append((p.name, None))
    return out


def total_length(tracks):
    """The summed length in seconds of the tracks of tracks_of(), None when one is unknown."""
    lengths = [values[fields.LENGTH] if values else None for _, values in tracks]
    return None if not lengths or None in lengths else sum(lengths)


def joined(values):
    return "; ".join(values)


def common(tracks):
    """{album field: the value every readable track holds, or None when they differ}."""
    out = {}
    for name in ALBUM_FIELDS:
        seen = {joined(values[name]) for _, values in tracks if values}
        out[name] = seen.pop() if len(seen) == 1 else None
    return out


def spread(tracks):
    """{album field: [(value, [(position among the readable tracks, title)])], the most held
    value first} of the album fields whose tracks differ."""
    readable = [(name, values) for name, values in tracks if values]
    out = {}
    for field in ALBUM_FIELDS:
        held = defaultdict(list)
        for i, (name, values) in enumerate(readable):
            held[joined(values[field])].append((i, joined(values["title"]) or name))
        if len(held) > 1:
            out[field] = sorted(held.items(), key=lambda kept: (-len(kept[1]), not kept[0], kept[0]))
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


def with_mixed(albums):
    """The albums whose tracks differ on an album field, those differing on the most fields first."""
    return sorted((a for a in albums if a.mixed), key=lambda a: (-len(a.mixed), a.folder))


def with_dates(albums):
    """The albums whose tracks' years break the rule of dates, those with the most problems first."""
    return sorted((a for a in albums if a.dates), key=lambda a: (-len(a.dates), a.folder))


def with_repeats(albums):
    """The albums with tracks whose fields hold a value twice or several values, the most such
    tracks first."""
    return sorted((a for a in albums if a.repeated), key=lambda a: (-a.repeated, a.folder))


def disc_of(values):
    """The disc a track's fields put it on, "1" without a disc number."""
    return (values["disc"] or ["1"])[0]


def disc_names(tracks):
    """{disc: the name every readable track of it holds, or None when they differ}, in track order."""
    seen = {}
    for _, values in tracks:
        if values:
            seen.setdefault(disc_of(values), set()).add(joined(values["discsubtitle"]))
    return {disc: names.pop() if len(names) == 1 else None for disc, names in seen.items()}


def several_discs(tracks):
    """Whether the album page opens with the discs shown: not for one disc numbered 1 or not at
    all, without a name."""
    return (any(values and (values["disc"] not in ([], ["1"]) or values["discsubtitle"])
                for _, values in tracks)
            or common(tracks)["disctotal"] not in ("", "1"))


def has_groupings(tracks):
    """Whether the album page opens with the groupings shown: when a track has one."""
    return any(values and values["grouping"] for _, values in tracks)


def seen_state(tracks):
    """What the album page shows, carried by its form and compared on submit."""
    return json.dumps({"album": common(tracks), "discs": disc_names(tracks),
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
    applies to every track, a disc's name to every track of the disc; one left empty while its
    tracks differed (shown None) is left alone."""
    out = []
    tracks = seen["tracks"]
    for name in ALBUM_FIELDS:
        value, shown = form.get(f"album.{name}", "").strip(), seen["album"][name]
        if (not value and shown is None) or (shown is not None and same(name, value, shown)):
            continue
        out += [[str(Path(folder_rel) / file), name, value, values[name]] for file, values in tracks]
    out += physical_edits(folder_rel, form, tracks)
    for disc, shown in seen["discs"].items():
        value = form.get(f"d{disc}.discsubtitle")
        if value is None or (not value.strip() and shown is None) or value.strip() == shown:
            continue
        out += [[str(Path(folder_rel) / file), "discsubtitle", value.strip(), values["discsubtitle"]]
                for file, values in tracks if disc_of(values) == disc]
    for i, (file, values) in enumerate(tracks):
        for name in TRACK_FIELDS:
            value = form.get(f"t{i}.{name}")
            if value is not None and not same(name, value.strip(), joined(values[name])):
                out.append([str(Path(folder_rel) / file), name, value.strip(), values[name]])
        text = form.get(f"t{i}.lyrics")
        if text is not None and fields.LYRICS in values:
            value = INSTRUMENTAL if form.get(f"t{i}.instrumental") else text
            if fields.check(fields.LYRICS, [value]) != values[fields.LYRICS]:
                out.append([str(Path(folder_rel) / file), fields.LYRICS, value, values[fields.LYRICS]])
        if form.get(f"t{i}.replaygain_delete") and values.get(fields.REPLAYGAIN):
            out.append([str(Path(folder_rel) / file), fields.REPLAYGAIN, "", values[fields.REPLAYGAIN]])
    return out


def physical_edits(folder_rel, form, tracks):
    """The comment edits that give every track PHYSICAL, or take it away, when the form's box no
    longer says what the tracks hold: ticked when every one holds it."""
    if not form.get("album.physical_field"):
        return []
    wanted = form.get("album.physical") == "1"
    if wanted == all(marks.marked(values["comment"]) for _, values in tracks):
        return []
    out = []
    for file, values in tracks:
        if marks.marked(values["comment"]) != wanted:
            new = marks.with_marker(values["comment"]) if wanted else marks.without_marker(values["comment"])
            out.append([str(Path(folder_rel) / file), "comment", "\n".join(new), values["comment"]])
    return out


def preview_changes(preview, root: Path, shown=fields.brief):
    """What an album's page shows of a tag.Preview: the changes, [(field, before, after, [file
    names])] one per distinct change, the values as shown(field, values) gives them, the folders
    the tracks move between, [(from, to)], relative to `root`, the number of files renamed in their
    folder, and the problem lines."""
    groups = defaultdict(list)
    for path, name, old, new in preview.writes:
        groups[(name, shown(name, old), shown(name, new))].append(path.name)
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


def with_lyrics(tracks):
    """{file name: "instrumental" when its lyrics are the INSTRUMENTAL mark, else whether it has
    some} of the tracks of tracks_of()."""
    out = {}
    for name, values in tracks:
        text = values[fields.LYRICS] if values else []
        out[name] = "instrumental" if text and is_instrumental(text[0]) else bool(text)
    return out


def without_lyrics(albums, later=None):
    """The albums with tracks without lyrics, neither instrumental nor awaited, the most such
    tracks first, those of `later` ({folder: when it was put off}) after the others, the one put
    off longest first."""
    later = later or {}
    return sorted((a for a in albums if a.without_lyrics),
                  key=lambda a: (later.get(a.folder, 0.0), -a.without_lyrics, a.folder))


def awaited_stamp(state_dir: Path):
    """What changes when the awaited tracks do: the time and size of their file, None without one."""
    try:
        st = os.stat(state_dir / awaited.FILE)
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size
