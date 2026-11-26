"""
physical — keeps the albums marked PHYSICAL and the Discogs collection in step, both ways.

The collection of the owner of $DISCOGS_TOKEN is read, every folder (physical/collection.py),
and each release is matched to an album of the library (physical/matching.py): the album whose
tracks hold its Discogs release id, else the album of the same artist and title, whatever the
edition. An album only close to a release is a question for the dashboard (Reviews).

An album of the library is physical when one of its tracks holds the word PHYSICAL in its
comment, however it got there; the pass writes it into every track, the field Virtual Library
Creator filters on in Lyrion, with the id of its release (DISCOGS_RELEASE_ID) where none is.
Each applied pass keeps what both sides agreed on (physical.json), so that the next one tells
which side changed and carries the change over to the other:
  - a release added to the collection marks its album, one taken out unmarks it;
  - an album marked adds its release to the collection (its id, else the edition picked on
    the dashboard), one unmarked takes its release out.
An album never seen agreeing, marked but not in the collection, is a question for the dashboard.
The id, once written, is never removed by a pass.

  music-librarian physical            # dry run: reads Discogs, shows what each side would get
  music-librarian physical --apply    # write the tags, add to and take out of the collection

The dry run reads Discogs (the collection, nothing else) and keeps it in the state folder
(physical-collection.json) for the dashboard. Every tag change goes through the journal, which
`music-librarian undo` restores; the collection's changes are only logged.

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


AGREE, MARK, UNMARK, ADD, REMOVE, EDITION, CONFLICT = (
    "agree", "mark", "unmark", "add", "remove", "edition", "conflict")
PENDING = (EDITION, CONFLICT)
DONE = {MARK: "marked", UNMARK: "unmarked", ADD: "added to Discogs", REMOVE: "taken out of Discogs"}


@dataclass
class Change:  # pylint: disable=too-many-instance-attributes  # one per part of what an album gets
    album: LibraryAlbum
    action: str           # one of AGREE … CONFLICT
    physical: bool        # what the album is once done; for a PENDING one, as it is now
    release: dict | None  # the release of the collection matched to it
    how: str | None       # how it was matched (matching.ID, NAME, ANSWER)
    release_id: str = ""  # the release to add to the collection, or whose id the tracks get
    marker: list = field(default_factory=list)   # the tracks whose comment gains or loses PHYSICAL
    ids: list = field(default_factory=list)      # the tracks to hold release_id


def decide(marked, release, synced, conflict):
    """What an album gets, `marked` whether the library holds it physical, `release` the one
    matched in the collection, `synced` what the last pass left it (None: never seen agreeing)
    and `conflict` the dashboard's answer about it."""
    owned = release is not None
    if marked == owned:
        return AGREE
    if synced is None:
        return MARK if owned else {"add": ADD, "drop": UNMARK}.get(conflict, CONFLICT)
    if synced == owned:
        return ADD if marked else REMOVE
    return MARK if owned else UNMARK


def plan(albums, tracks, releases, held):
    """([Change] of every album, Matches), `held` the file of marks.load()."""
    matches = match(releases, albums, held["answers"])
    changes = []
    for album in albums:
        release, how = matches.found.get(album.folder, (None, None))
        mine = tracks[album.folder]
        marked = any(marks.marked(t.comment) for t in mine)
        synced = held["synced"].get(album.key)
        action = decide(marked, release, synced, held["conflicts"].get(album.key))
        if action == UNMARK and synced is None:
            how = ANSWER
        release_id = release["id"] if release else ""
        if action == ADD:
            release_id = next((i for t in mine for i in t.ids), "") or held["editions"].get(album.key, "")
            action = ADD if release_id else EDITION
        if action in PENDING:
            changes.append(Change(album, action, marked, release, how))
            continue
        physical = {AGREE: marked, MARK: True, ADD: True}.get(action, False)
        change = Change(album, action, physical, release, how, release_id if physical else "",
                        [t for t in mine if marks.marked(t.comment) != physical])
        change.ids = [t for t in mine if not t.ids] if change.release_id else []
        changes.append(change)
    return changes, matches


def shown(release):
    formats = ", ".join(release.get("formats") or [])
    detail = ", ".join(x for x in (formats, release.get("year")) if x)
    found = f"Discogs {release['id']}: {release['artist']} – {release['title']}"
    return f"{found} ({detail})" if detail else found


REASONS = {ID: "its Discogs id", ANSWER: "answered on the dashboard"}


def describe(change, log):
    rel = change.album.folder
    if change.action == MARK:
        why = REASONS.get(change.how)
        log(f"PHYSICAL {rel}\n         ← {shown(change.release)}" + (f"  [{why}]" if why else ""))
    elif change.action == UNMARK:
        why = "answered on the dashboard" if change.how == ANSWER else "not in the Discogs collection"
        log(f"UNMARK   {rel}  ({why})")
    elif change.action == ADD:
        log(f"ADD      {rel}\n         → Discogs {change.release_id}, added to the collection")
    elif change.action == REMOVE:
        log(f"REMOVE   {rel}\n         → {shown(change.release)}, taken out of the collection")
    elif change.action == EDITION:
        log(f"EDITION  {rel}  (marked PHYSICAL, no Discogs id: pick its edition on the dashboard)")
    elif change.action == CONFLICT:
        log(f"CONFLICT {rel}  (marked PHYSICAL, not in the Discogs collection: answer on the dashboard)")
    elif change.marker and change.physical:
        log(f"PHYSICAL {rel}  ({count(len(change.marker), 'track')} more)")
    if change.ids and change.action not in (MARK, ADD):
        log(f"DISCOGS  {rel}  ← release {change.release_id} ({count(len(change.ids), 'track')})")


def write_tags(change, journal, log, stats):
    """Write the change's tags, each journaled; False when one failed."""
    done = True
    edits = [(t.path, COMMENT) for t in change.marker] + [(t.path, RELEASE) for t in change.ids]
    for path, name in edits:
        try:
            old = fields.read_field(path, name)
            if name == COMMENT:
                new = marks.with_marker(old) if change.physical else marks.without_marker(old)
            else:
                new = old or [change.release_id]
            if new == old:
                continue
            fields.write(path, name, new)
        except Exception as e:
            log(f"ERROR    ({e})  {path}")
            stats["errors"] += 1
            done = False
            continue
        journal.tag(path, name, old, new)
        stats["ids written" if name == RELEASE else "tags written"] += 1
    return done


def write(changes, user, journal, log, stats):
    """Carry each change over, the collection first, then the tags; ({album key: what it now is}
    of the changes done, True if a stop request cut them short)."""
    done = {}
    for i, change in enumerate(changes):
        if STOP.is_set():
            log(f"STOP     requested: {count(len(changes) - i, 'album')} left for the next pass")
            return done, True
        try:
            if change.action == ADD:
                collection.add(user, change.release_id)
            elif change.action == REMOVE:
                collection.remove(user, change.release)
        except ProviderUnavailable as e:
            log(f"ERROR    (Discogs: {e})  {change.album.folder}")
            stats["errors"] += 1
            continue
        if write_tags(change, journal, log, stats):
            done[change.album.key] = change.physical
            if change.action in DONE:
                stats[DONE[change.action]] += 1
    return done, False


def summary(stats, changes, matches, apply, log):
    acting = lambda action: sum(1 for c in changes if c.action == action)
    if not apply:
        stats["to mark"], stats["to unmark"] = acting(MARK), acting(UNMARK)
        stats["to add to Discogs"], stats["to take out"] = acting(ADD), acting(REMOVE)
        stats["ids to write"] = sum(len(c.ids) for c in changes)
    stats["editions to pick"], stats["conflicts"] = acting(EDITION), acting(CONFLICT)
    stats["to review"] = len(matches.doubtful)
    stats["not in the library"] = len(matches.absent)
    log("\n--- Physical summary ---")
    for k in ("releases", "albums", "physical", "to mark", "marked", "to unmark", "unmarked",
              "to add to Discogs", "added to Discogs", "to take out", "taken out of Discogs",
              "ids to write", "tags written", "ids written", "editions to pick", "conflicts", "to review",
              "not in the library", "skipped", "errors"):
        if stats[k]:
            log(f"{k:>20}: {stats[k]}")
    if stats["editions to pick"] or stats["conflicts"] or matches.doubtful:
        log(f"{'answer':>20}: on the dashboard, Reviews")


def physical_pass(opts: Options, log):
    """A physical pass, without taking the lock: the caller holds it when `opts.apply`."""
    started = time.time()
    mode = "APPLY" if opts.apply else "DRY-RUN"
    log(f"[{mode}] syncing the physical albums under {opts.root} with Discogs")
    stats = defaultdict(int)
    try:
        user, releases = collection.fetch()
    except (collection.CollectionError, ProviderUnavailable) as e:
        log(f"ERROR    the Discogs collection cannot be read: {e}")
        return 2
    collection.keep(opts.state_dir, user, releases)
    log(f"DISCOGS  collection of {user}: {count(len(releases), 'release')}")
    albums, tracks = library(opts, log, stats)
    held = marks.load(opts.state_dir)
    changes, matches = plan(albums, tracks, releases, held)
    if not releases and any(c.action == UNMARK for c in changes):
        kept = sum(1 for c in changes if c.action == UNMARK)
        log(f"WARNING  empty collection: {count(kept, 'album')} left marked")
        changes = [c for c in changes if c.action != UNMARK]
    acting = [c for c in changes if c.action not in PENDING and (c.action != AGREE or c.marker or c.ids)]
    for change in sorted(changes, key=lambda c: c.album.folder):
        describe(change, log)
    for release, album in matches.doubtful:
        log(f"REVIEW   {album.folder}\n         ~ {shown(release)}")
    for release in sorted(matches.absent, key=lambda r: (r["artist"].casefold(), r["title"].casefold())):
        log(f"ABSENT   {shown(release)}")

    journal = Journal(opts.state_dir, dict(root=str(opts.root), started=started, argv=sys.argv[1:]))
    stopped = False
    if opts.apply:
        if acting:
            lyrion.await_scan(opts.lyrion, True, log)
        done, stopped = write(acting, user, journal, log, stats)
        busy = {id(c) for c in acting}
        done.update({c.album.key: c.physical for c in changes if c.action == AGREE and id(c) not in busy})
        marks.update(opts.state_dir, "synced", done)
        marks.update(opts.state_dir, "editions", {key: None for key in done if key in held["editions"]})
        marks.update(opts.state_dir, "conflicts", {key: None for key in done if key in held["conflicts"]})
        with MetadataCache(opts.state_dir) as cache:
            for change in acting:
                for t in {t.path: t for t in change.marker + change.ids}.values():
                    cache.snapshot(t.path)
    journal.close()
    if opts.apply and not stopped:
        write_stamp(opts.state_dir, LAST_RUN, started)
    stats["releases"], stats["albums"] = len(releases), len(albums)
    stats["physical"] = sum(c.physical for c in changes)
    summary(stats, changes, matches, opts.apply, log)
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
