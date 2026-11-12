"""
tag — rewrites tag fields of tracks, then files them again where their new tags put them.

  music-librarian tag PATH... --set FIELD=VALUE [--set FIELD=VALUE…]   # dry run
  music-librarian tag "Beyonce/4" --set albumartist=Beyoncé --apply
  music-librarian tag PATH... --disc-names [--apply]

PATH is a track or a folder (every track under it) of the library. FIELD is one of title,
artist, albumartist, album, track, tracktotal, disc, disctotal, discsubtitle (the disc's name),
grouping, year, genre, composer and compilation (1 or 0); an empty VALUE removes the field. A genre takes
the spelling of the validated list (state/genres.txt) when the list knows it. replaygain only
takes an empty VALUE: it removes every REPLAYGAIN_* tag, which the next pass of
enrich.replaygain measures again.

--disc-names copies the grouping into the disc name of the tracks without one, on the albums
of several discs whose discs each hold a grouping of their own (Apple Music shows only the
grouping, Lyrion takes the disc name from discsubtitle); the others are reported as KEPT.

The dashboard also edits the lyrics through it (field lyrics): a track whose lyrics change is
looked up again by the next pass of enrich.lyrics, removed lyrics without waiting. It replaces an
album's cover through it too, as `cover` does, before the album is filed again.

The folders of the edited tracks are then filed by organize (--no-refile to leave them), the
tag changes and the moves going to the same journal: `music-librarian undo` restores both.
The dry run shows the tags to write and the moves they lead to.

Exit codes: 0 = OK, 2 = refused, skipped or failed edits (or organize conflicts), 3 = another
pass is running, 4 = stopped before the end, 5 = Lyrion carry-over failed.
"""
import argparse
import shlex
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from music_librarian import cover, organize, repeats
from music_librarian.core import arguments
from music_librarian.core.files import AUDIO_EXT, is_junk, prune_empty, relative
from music_librarian.core.journal import Journal
from music_librarian.core.lock import EXIT_STOPPED, STOP, handle_stop_signals
from music_librarian.core.log import Buffer, Log, count
from music_librarian.core.runs import recorded
from music_librarian.core.state import state_path
from music_librarian.enrich import genres
from music_librarian.integrations import lyrion
from music_librarian.integrations.lyrion import Lyrion
from music_librarian.lyrics.misses import Misses
from music_librarian.media import fields
from music_librarian.media.metadata_cache import MetadataCache
from music_librarian.organize.scan import collect
from music_librarian.split.library import run_locked

EDITABLE = tuple(f for f in fields.FIELDS if not f.endswith("_mbid")) + (fields.REPLAYGAIN,)


@dataclass
class Edit:
    """One field of one track set to `value` ("" removes it). With `seen`, the values the field
    held when the edit was made: a track holding others since is refused."""
    path: Path
    field: str
    value: str
    seen: list | None = None


@dataclass
class Options:
    root: Path
    edits: list = field(default_factory=list)
    apply: bool = False
    state_dir: Path = field(default_factory=state_path)
    lyrion: Lyrion | None = None
    refile: bool = True
    disc_names: list = field(default_factory=list)   # the tracks --disc-names looks at
    cover: tuple | None = None                       # (album folder, image) to put as its folder.jpg

    def __post_init__(self):
        self.root = Path(self.root).resolve()
        for edit in self.edits:
            edit.path = Path(edit.path).resolve()
        if self.cover is not None:
            self.cover = tuple(Path(p).resolve() for p in self.cover)


def shown(values, name=None):
    return fields.brief(name, values)


def filing_tags(values):
    """fields.read() values as organize files by them (organize.scan.read_tags)."""
    first = lambda name: values[name][0] if values[name] else None
    number = lambda name: int(values[name][0]) if values[name] and values[name][0].isdigit() else None
    return dict(albumartist=first("albumartist"), artist=first("artist"), album=first("album"),
                title=first("title"), track=number("track"), disc=number("disc"),
                disctotal=number("disctotal"), compilation=bool(values["compilation"]))


def genre_list(state_dir, log):
    """The validated genre list as enrich.genres loads it, or None without one."""
    path = genres.list_path(state_dir)
    if not path.exists():
        return None
    try:
        return genres.load(path)
    except genres.GenreListError as e:
        log(f"ERROR    {e}: genres are written as given")
        return None


def plan(opts, log, stats):
    """([(path, field, old values, new values)], {path: tags as they will be, for organize})."""
    mapping = genre_list(opts.state_dir, log)
    by_path = defaultdict(list)
    for edit in opts.edits:
        by_path[edit.path].append(edit)
    writes, planned = [], {}
    for path, edits in by_path.items():
        stats["tracks"] += 1
        rel = relative(path, opts.root)
        if not path.is_relative_to(opts.root) or path.suffix.lower() not in AUDIO_EXT:
            log(f"SKIPPED  (not a track of the library)  {rel}")
            stats["skipped"] += 1
            continue
        try:
            current = fields.read(path)
            for name in {edit.field for edit in edits} & {fields.LYRICS, fields.REPLAYGAIN}:
                current[name] = fields.read_field(path, name)
        except Exception as e:
            log(f"SKIPPED  (unreadable: {e})  {rel}")
            stats["skipped"] += 1
            continue
        after = dict(current)
        for edit in edits:
            try:
                new = fields.check(edit.field, [edit.value])
                if (why := repeats.refusal(edit.field, new)) is not None:
                    raise fields.FieldError(why)
            except fields.FieldError as e:
                log(f"ERROR    ({e})  {rel}")
                stats["errors"] += 1
                continue
            if edit.field == "genre" and new and mapping:
                validated = genres.resolve(new, mapping)
                if validated is None:
                    log(f"UNKNOWN  genre \"{new[0]}\" is not in the validated list  {rel}")
                new = [validated] if validated else new
            if edit.seen is not None and current[edit.field] != [v.strip() for v in edit.seen if v.strip()]:
                held = shown(current[edit.field], edit.field)
                log(f"REFUSED  ({edit.field} changed since: \"{held}\")  {rel}")
                stats["refused"] += 1
                continue
            if new == current[edit.field]:
                stats["unchanged"] += 1
                continue
            log(f"TAG      {edit.field}: \"{shown(current[edit.field], edit.field)}\" → "
                f"\"{shown(new, edit.field)}\"  {rel}")
            writes.append((path, edit.field, current[edit.field], new))
            after[edit.field] = new
        planned[path] = filing_tags(after)
    return writes, planned


def cached(written, moved, state_dir):
    """The tracks written, (path, field, values), read again into the metadata cache where they
    now are (`moved`: {source: destination}); those whose lyrics changed no longer recorded as
    looked up in vain."""
    with MetadataCache(state_dir) as cache, Misses(state_dir) as misses:
        for path, name, _new in written:
            path = moved.get(path, path)
            cache.snapshot(path)
            if name == fields.LYRICS:
                misses.forget(path)


PROBLEMS = {"REFUSED", "ERROR", "SKIPPED", "CONFLICT", "UNKNOWN"}


@dataclass
class Preview:
    """What a tag pass would do: `writes` as plan() returns them, `moves` (src, dst) the refiling
    they lead to, `problems` the lines logged for what it would refuse or leave alone."""
    writes: list
    moves: list
    problems: list


def preview(opts: Options):
    """A dry run of the pass, logged nowhere: nothing is written but the metadata cache."""
    out, stats = Buffer(), defaultdict(int)
    writes, planned = plan(opts, out, stats)
    moves = []
    if opts.refile and writes:
        moves = organize.organize_pass(organize.Options(
            root=opts.root, paths=sorted({path.parent for path, *_ in writes}), state_dir=opts.state_dir,
            interruptible=False, overrides=planned), out).moves
    return Preview(writes, moves, problem_lines(out.lines))


def problem_lines(lines):
    """The lines tagged as a problem, each with the indented lines that continue it."""
    out, keep = [], False
    for line in lines:
        if line[:1].isspace() and line.strip():
            if keep:
                out[-1] += "\n" + line
            continue
        keep = line[:8].strip() in PROBLEMS
        if keep:
            out.append(line)
    return out


def tag_pass(opts: Options, log):
    """A tag pass, without taking the lock: the caller holds it when `opts.apply`."""
    started = time.time()
    log(f"[{'APPLY' if opts.apply else 'DRY-RUN'}] tagging under {opts.root}")
    stats = defaultdict(int)
    opts.edits += disc_names(opts, log, stats)
    writes, planned = plan(opts, log, stats)
    placing = cover_planned(opts, log, stats)
    journal = Journal(opts.state_dir, dict(root=str(opts.root), started=started, argv=sys.argv[1:]))
    stopped, written = False, []
    lyrion.await_scan(opts.lyrion, opts.apply and bool(writes or placing is not None), log)
    if opts.apply:
        for i, (path, name, old, new) in enumerate(writes):
            if STOP.is_set():
                log(f"STOP     requested: {count(len(writes) - i, 'tag')} not written, nor any track filed")
                stopped = True
                break
            try:
                fields.write(path, name, new)
            except Exception as e:
                log(f"ERROR    ({e})  {relative(path, opts.root)}")
                stats["errors"] += 1
                continue
            journal.tag(path, name, old, new)
            written.append((path, name, new))
            stats["written"] += 1
    if placing is not None and not stopped:
        if not opts.apply:
            cover.would(placing, log)
            stats["to embed"] = len(placing.outdated)
        elif STOP.is_set():
            log("STOP     requested: the cover is left as it was, nor any track filed")
            stopped = True
        else:
            failures = cover.place(placing, opts.state_dir, journal, log)
            stats["embedded"] = len(placing.outdated) - failures
            stats["errors"] += failures

    report = None
    if opts.refile and writes and not stopped:
        log("")
        report = organize.organize_pass(organize.Options(
            root=opts.root, paths=sorted({path.parent for path, *_ in writes}), apply=opts.apply,
            state_dir=opts.state_dir, lyrion=opts.lyrion, interruptible=False,
            overrides={} if opts.apply else planned), log, journal)
    journal.close()
    if written:
        cached(written, dict(report.done) if report and opts.apply else {}, opts.state_dir)
    if report and opts.apply:
        with MetadataCache(opts.state_dir) as cache:
            for _, dst in report.done:
                if dst.suffix.lower() in AUDIO_EXT:
                    cache.snapshot(dst)
        # organize keeps the folders it is given, which the edited tracks may have emptied
        for folder in sorted({src.parent for src, _ in report.done}, key=lambda d: -len(d.parts)):
            stats["folders removed"] += prune_empty(folder, {opts.root})

    stats["tags to write"] = len(writes)
    if report:
        stats["moved" if opts.apply else "to move"] = len(report.done if opts.apply else report.moves)
    log("\n--- Tag summary ---")
    for k in ("tracks", "tags to write", "written", "unchanged", "to embed", "embedded", "to move", "moved",
              "folders removed", "refused", "skipped", "errors"):
        if stats[k]:
            log(f"{k:>20}: {stats[k]}")
    if journal.path:
        log(f"{'journal':>20}: {journal.path}")
        log(f"{'undo':>20}: music-librarian undo {shlex.quote(str(journal.path))} "
            f"--root {shlex.quote(str(opts.root))} --apply")
        if report and report.code == lyrion.EXIT_LYRION:
            log(f"{'Lyrion retry':>20}: music-librarian organize --lyrion-replay "
                f"{shlex.quote(str(journal.path))} --apply")
    if stopped:
        return EXIT_STOPPED
    if stats["refused"] or stats["skipped"] or stats["errors"]:
        return 2
    return report.code if report else 0


def cover_planned(opts, log, stats):
    """The cover.Placing of opts.cover, or None without one or once refused."""
    if opts.cover is None:
        return None
    folder, image = opts.cover
    rel = relative(folder, opts.root)
    placing = None
    if folder.is_relative_to(opts.root):
        placing = cover.planned(folder, image, rel, opts.state_dir, log)
    else:
        log(f"REFUSED  (not an album folder of the library)  {rel}")
    if placing is None:
        stats["refused"] += 1
    return placing


def run(opts: Options, log):
    """A tag pass, under the lock with `apply`."""
    return run_locked(tag_pass, opts, log)


def tracks(paths):
    """The tracks of each path: the path itself, or every track under a folder."""
    out = []
    for p in paths:
        p = Path(p).resolve()
        if p.is_dir():
            out += [f for f in collect([p]) if f.suffix.lower() in AUDIO_EXT and not is_junk(f)]
        else:
            out.append(p)
    return out


def grouped(opts, log, stats):
    """The tracks of opts.disc_names in folders where the metadata cache finds a grouping."""
    snapshots = []
    with MetadataCache(opts.state_dir) as cache:
        for path in opts.disc_names:
            rel = relative(path, opts.root)
            if (not path.is_relative_to(opts.root) or path.suffix.lower() not in AUDIO_EXT
                    or not path.is_file()):
                log(f"SKIPPED  (not a track of the library)  {rel}")
                stats["skipped"] += 1
            elif (snap := cache.snapshot(path)) is None:
                log(f"SKIPPED  (unreadable)  {rel}")
                stats["skipped"] += 1
            else:
                snapshots.append((path, snap))
    folders = {path.parent for path, snap in snapshots if snap["tags"].get("grouping")}
    return [path for path, _ in snapshots if path.parent in folders]


def disc_names(opts, log, stats):
    """The edits of --disc-names, folder by folder."""
    root = opts.root
    albums = defaultdict(list)
    for path in grouped(opts, log, stats):
        try:
            albums[path.parent].append((path, fields.read(path)))
        except Exception as e:
            log(f"SKIPPED  (unreadable: {e})  {relative(path, root)}")
            stats["skipped"] += 1
    edits = []
    for folder, album in sorted(albums.items()):
        if not any(values["grouping"] for _, values in album):
            continue
        discs = defaultdict(set)
        for _, values in album:
            discs[(values["disc"] or ["1"])[0]].add(tuple(values["grouping"]))
        groupings = [next(iter(g)) for g in discs.values() if len(g) == 1]
        if (len(discs) < 2 or len(groupings) < len(discs) or len(set(groupings)) < len(groupings)
                or any(len(g) != 1 for g in groupings)):
            log(f"KEPT     (grouping not one per disc)  {relative(folder, root)}")
            continue
        edits += [Edit(path, "discsubtitle", values["grouping"][0], seen=[])
                  for path, values in album if not values["discsubtitle"]]
    return edits


def setting(s):
    """'FIELD=VALUE' -> (field, value)."""
    name, sep, value = s.partition("=")
    if not sep or name not in EDITABLE:
        raise argparse.ArgumentTypeError(f"expected FIELD=VALUE, FIELD among {', '.join(EDITABLE)}: {s!r}")
    try:
        fields.check(name, [value])
    except fields.FieldError as e:
        raise argparse.ArgumentTypeError(str(e)) from None
    if (why := repeats.refusal(name, [value])) is not None:
        raise argparse.ArgumentTypeError(why)
    return name, value


def main(argv=None):
    ap = arguments.parser("tag", "Rewrite tag fields of tracks, then file them again.")
    arguments.add_root(ap)
    ap.add_argument("paths", nargs="+", help="tracks or folders of the library")
    what = ap.add_mutually_exclusive_group(required=True)
    what.add_argument("--set", dest="settings", type=setting, action="append",
                      metavar="FIELD=VALUE", help="the value to write (empty: remove the field)")
    what.add_argument("--disc-names", action="store_true",
                      help="copy the grouping into the disc name, on albums of one grouping per disc")
    ap.add_argument("--no-refile", action="store_true", help="leave the edited tracks where they are")
    arguments.add_apply(ap, "write the tags")
    lyrion.add_arguments(ap)
    arguments.add_state_dir(ap)
    arguments.add_syslog(ap)
    args = ap.parse_args(argv)
    found = tracks(args.paths)
    opts = Options(root=args.root, apply=args.apply, state_dir=state_path(args.state_dir),
                   refile=not args.no_refile,
                   edits=[Edit(p, name, value) for p in found for name, value in args.settings or []],
                   disc_names=found if args.disc_names else [])
    opts.lyrion = lyrion.from_args(args, opts.root, opts.state_dir)
    log = Log(args.syslog)
    return recorded(lambda: run(opts, log), log, opts.state_dir, "tag", opts.apply)


if __name__ == "__main__":
    handle_stop_signals()
    sys.exit(main())
