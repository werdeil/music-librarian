"""
physical — marks the albums owned on a physical medium, those of the Discogs collection.

The collection of the owner of $DISCOGS_TOKEN is read, every folder (physical/collection.py),
and each release is matched to an album of the library (physical/matching.py): the album whose
tracks hold its Discogs release id, else the album of the same artist and title, whatever the
edition. An album only close to a release is a question for the dashboard (Reviews), answered
yes or no.

A physical album holds the word PHYSICAL in the comment of each of its tracks, the field
Virtual Library Creator filters on in Lyrion, and the id of its release (DISCOGS_RELEASE_ID)
in the tracks that hold none. An album marked or unmarked by hand on the dashboard keeps that
mark whatever Discogs holds; any other loses PHYSICAL once its release leaves the collection.
The id, once written, is never removed by a pass.

  music-librarian physical            # dry run: reads Discogs, shows the tags to write
  music-librarian physical --apply    # write them

The dry run reads Discogs (its metadata, nothing else) and keeps the collection read in the
state folder (physical-collection.json) for the dashboard. Every tag change goes through the
journal: `music-librarian undo` restores it.

Exit codes: 0 = OK, 2 = collection unreadable, unreadable files or failed writes, 3 = another
pass is running, 4 = stopped before the end.
"""
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from music_librarian.core import arguments
from music_librarian.core.files import AUDIO_EXT, is_junk, relative
from music_librarian.core.journal import Journal, finish
from music_librarian.core.lock import STOP, handle_stop_signals
from music_librarian.core.log import Log, count
from music_librarian.core.runs import recorded
from music_librarian.core.state import read_stamp, state_path, write_stamp
from music_librarian.editions.common import ProviderUnavailable
from music_librarian.integrations import lyrion
from music_librarian.integrations.lyrion import Lyrion
from music_librarian.media import fields
from music_librarian.media.metadata_cache import MetadataCache
from music_librarian.organize.scan import collect
from music_librarian.physical import collection, marks
from music_librarian.physical.matching import ANSWER, ID, LibraryAlbum, match
from music_librarian.split.library import run_locked

LAST_RUN = "physical_last_run"
COMMENT, RELEASE = "comment", "discogs_release"


@dataclass
class Options:
    root: Path
    state_dir: Path = field(default_factory=state_path)
    apply: bool = False
    lyrion: Lyrion | None = None

    def __post_init__(self):
        self.root = Path(self.root).resolve()


@dataclass
class Track:
    path: Path
    comment: list
    ids: list


def first(tags, *names):
    return next((v for n in names for v in tags.get(n, []) if v.strip()), "")


def from_snapshot(path, snap):
    """(album artist, album, Track) of a metadata cache snapshot."""
    tags = {k.lower(): v for k, v in snap["tags"].items()}
    values = lambda name: [v for v in tags.get(name, []) if v.strip()]
    return (first(tags, "albumartist", "album artist", "artist"), first(tags, "album"),
            Track(path, values("comment"), values("discogs_release_id")))


def albums_of(tracks, root):
    """([LibraryAlbum], {folder: [Track]}) of (path, artist, title, Track), one album per folder."""
    held = defaultdict(list)
    for path, artist, title, track in tracks:
        held[str(path.parent.relative_to(root))].append((artist, title, track))
    albums = []
    for folder, found in held.items():
        artist = next((a for a, _, _ in found if a), "")
        title = next((t for _, t, _ in found if t), "")
        albums.append(LibraryAlbum(folder, artist, title, {i for _, _, track in found for i in track.ids}))
    return albums, {folder: [track for _, _, track in found] for folder, found in held.items()}


def library(opts, log, stats):
    """albums_of() the library's tracks, read through the metadata cache."""
    found = []
    with MetadataCache(opts.state_dir) as cache:
        for p in collect([opts.root]):
            if is_junk(p) or p.suffix.lower() not in AUDIO_EXT:
                continue
            snap = cache.snapshot(p)
            if snap is None:
                log(f"SKIPPED  (unreadable)  {relative(p, opts.root)}")
                stats["skipped"] += 1
            else:
                found.append((p, *from_snapshot(p, snap)))
    return albums_of(found, opts.root)


@dataclass
class Change:
    album: LibraryAlbum
    physical: bool        # what the album is to be
    why: str              # ID, NAME, ANSWER, "manual", or "gone" once its release left the collection
    release: dict | None
    marker: list          # the tracks whose comment is to gain or lose PHYSICAL
    ids: list             # the tracks to hold the release's id


def plan(albums, tracks, releases, manual, answers):
    """([Change] of the albums whose tags are to change, Matches, {folder: whether physical})."""
    matches = match(releases, albums, answers)
    changes, physical = [], {}
    for album in albums:
        release, how = matches.found.get(album.folder, (None, None))
        by_hand = manual.get(album.key)
        wanted = by_hand if by_hand is not None else release is not None
        physical[album.folder] = wanted
        held = tracks[album.folder]
        marker = [t for t in held if marks.marked(t.comment) != wanted]
        ids = [t for t in held if not t.ids] if wanted and release else []
        if marker or ids:
            why = "manual" if by_hand is not None else how if release else "gone"
            changes.append(Change(album, wanted, why, release, marker, ids))
    return changes, matches, physical


def shown(release):
    formats = ", ".join(release.get("formats") or [])
    detail = ", ".join(x for x in (formats, release.get("year")) if x)
    found = f"Discogs {release['id']}: {release['artist']} – {release['title']}"
    return f"{found} ({detail})" if detail else found


REASONS = {ID: "its Discogs id", ANSWER: "answered on the dashboard", "manual": "marked by hand"}


def describe(change, log):
    rel = change.album.folder
    if change.marker and change.physical:
        source = shown(change.release) if change.release else "no release"
        why = REASONS.get(change.why)
        log(f"PHYSICAL {rel}\n         ← {source}" + (f"  [{why}]" if why else ""))
    elif change.marker:
        why = "unmarked by hand" if change.why == "manual" else "not in the Discogs collection"
        log(f"UNMARK   {rel}  ({why})")
    if change.ids and not (change.marker and change.physical):
        tracks = count(len(change.ids), "track")
        log(f"DISCOGS  {rel}  ← release {change.release['id']} ({tracks})")


def write(changes, journal, log, stats):
    """Write the changes, each tag journaled; True if a stop request cut them short."""
    for i, change in enumerate(changes):
        if STOP.is_set():
            log(f"STOP     requested: {count(len(changes) - i, 'album')} left for the next pass")
            return True
        edits = [(t.path, COMMENT) for t in change.marker] + [(t.path, RELEASE) for t in change.ids]
        for path, name in edits:
            try:
                old = fields.read_field(path, name)
                if name == COMMENT:
                    new = marks.with_marker(old) if change.physical else marks.without_marker(old)
                else:
                    new = old or [change.release["id"]]
                if new == old:
                    continue
                fields.write(path, name, new)
            except Exception as e:
                log(f"ERROR    ({e})  {path}")
                stats["errors"] += 1
                continue
            journal.tag(path, name, old, new)
            stats["ids written" if name == RELEASE else "tags written"] += 1
        if change.marker:
            stats["marked" if change.physical else "unmarked"] += 1
    return False


def physical_pass(opts: Options, log):
    """A physical pass, without taking the lock: the caller holds it when `opts.apply`."""
    started = time.time()
    log(f"[{'APPLY' if opts.apply else 'DRY-RUN'}] marking the physical albums under {opts.root}")
    stats = defaultdict(int)
    try:
        user, releases = collection.fetch()
    except (collection.CollectionError, ProviderUnavailable) as e:
        log(f"ERROR    the Discogs collection cannot be read: {e}")
        return 2
    collection.keep(opts.state_dir, user, releases)
    log(f"DISCOGS  collection of {user}: {count(len(releases), 'release')}")
    albums, tracks = library(opts, log, stats)
    manual, answers = marks.load(opts.state_dir)
    changes, matches, physical = plan(albums, tracks, releases, manual, answers)
    if not releases:
        kept = [c for c in changes if c.physical or c.why == "manual"]
        if len(kept) < len(changes):
            log(f"WARNING  empty collection: {count(len(changes) - len(kept), 'album')} left marked")
        changes = kept
    for change in sorted(changes, key=lambda c: c.album.folder):
        describe(change, log)
    for release, album in matches.doubtful:
        log(f"REVIEW   {album.folder}\n         ~ {shown(release)}")
    for release in sorted(matches.absent, key=lambda r: (r["artist"].casefold(), r["title"].casefold())):
        log(f"ABSENT   {shown(release)}")

    journal = Journal(opts.state_dir, dict(root=str(opts.root), started=started, argv=sys.argv[1:]))
    stopped = False
    if opts.apply and changes:
        lyrion.await_scan(opts.lyrion, True, log)
        stopped = write(changes, journal, log, stats)
        with MetadataCache(opts.state_dir) as cache:
            for change in changes:
                for t in {t.path: t for t in change.marker + change.ids}.values():
                    cache.snapshot(t.path)
    journal.close()
    if opts.apply and not stopped:
        write_stamp(opts.state_dir, LAST_RUN, started)

    stats["releases"] = len(releases)
    stats["albums"] = len(albums)
    stats["physical"] = sum(physical.values())
    if not opts.apply:
        stats["to mark"] = sum(1 for c in changes if c.physical and c.marker)
        stats["to unmark"] = sum(1 for c in changes if not c.physical and c.marker)
        stats["ids to write"] = sum(len(c.ids) for c in changes)
    stats["to review"] = len(matches.doubtful)
    stats["not in the library"] = len(matches.absent)
    log("\n--- Physical summary ---")
    for k in ("releases", "albums", "physical", "to mark", "marked", "to unmark", "unmarked", "ids to write",
              "tags written", "ids written", "to review", "not in the library", "skipped", "errors"):
        if stats[k]:
            log(f"{k:>20}: {stats[k]}")
    if matches.doubtful:
        log(f"{'review':>20}: on the dashboard, Reviews")
    return finish(journal, opts.root, log, stats, stopped)


def run(opts: Options, log):
    """A physical pass, under the lock with `apply`."""
    return run_locked(physical_pass, opts, log)


def last_run(state_dir: Path):
    return read_stamp(state_dir, LAST_RUN)


def main(argv=None):
    ap = arguments.parser("physical", "Mark the albums of the Discogs collection as physical.")
    arguments.add_root(ap)
    arguments.add_apply(ap, "write the tags")
    arguments.add_state_dir(ap)
    lyrion.add_arguments(ap)
    arguments.add_syslog(ap)
    args = ap.parse_args(argv)
    opts = Options(root=args.root, apply=args.apply, state_dir=state_path(args.state_dir))
    opts.lyrion = lyrion.from_args(args, opts.root, opts.state_dir)
    log = Log(args.syslog)
    return recorded(lambda: run(opts, log), log, opts.state_dir, "physical", opts.apply)


if __name__ == "__main__":
    handle_stop_signals()
    sys.exit(main())
