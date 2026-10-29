"""
tag — rewrites tag fields of tracks, then files them again where their new tags put them.

  music-librarian tag PATH... --set FIELD=VALUE [--set FIELD=VALUE…]   # dry run
  music-librarian tag "Beyonce/4" --set albumartist=Beyoncé --apply

PATH is a track or a folder (every track under it) of the library. FIELD is one of title,
artist, albumartist, album, track, tracktotal, disc, disctotal, year, genre, composer and
compilation (1 or 0); an empty VALUE removes the field. A genre takes the spelling of the
validated list (state/genres.txt) when the list knows it.

The folders of the edited tracks are then filed by organize (--no-refile to leave them), the
tag changes and the moves going to the same journal: `music-librarian undo` restores both.
The dry run shows the tags to write and the moves they lead to.

Exit codes: 0 = OK, 2 = refused, skipped or failed edits (or organize conflicts), 3 = another
pass is running, 4 = stopped before the end, 5 = Lyrion carry-over failed.
"""
import argparse
import os
import shlex
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from music_librarian import organize
from music_librarian.core.files import AUDIO_EXT, is_junk
from music_librarian.core.journal import Journal
from music_librarian.core.lock import EXIT_STOPPED, STOP, handle_stop_signals
from music_librarian.core.log import Log
from music_librarian.core.runs import recorded
from music_librarian.core.state import state_path
from music_librarian.enrich import genres
from music_librarian.integrations import lyrion
from music_librarian.integrations.lyrion import Lyrion
from music_librarian.media import fields
from music_librarian.organize.scan import collect
from music_librarian.split.library import run_locked

EDITABLE = tuple(f for f in fields.FIELDS if not f.endswith("_mbid"))


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

    def __post_init__(self):
        self.root = Path(self.root).resolve()
        for edit in self.edits:
            edit.path = Path(edit.path).resolve()


def shown(values):
    return "; ".join(values) or "(none)"


def filing_tags(values):
    """fields.read() values as organize files by them (organize.scan.read_tags)."""
    first = lambda name: values[name][0] if values[name] else None
    number = lambda name: int(values[name][0]) if values[name] and values[name][0].isdigit() else None
    return dict(albumartist=first("albumartist"), artist=first("artist"), album=first("album"),
                title=first("title"), track=number("track"), disc=number("disc"),
                disctotal=number("disctotal"), compilation=bool(values["compilation"]))


def genre_list(state_dir, log):
    """The validated genre list as enrich.genres loads it, or None without one."""
    path = Path(os.environ.get("MUSIC_LIBRARIAN_GENRES") or state_dir / "genres.txt")
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
        if not path.is_relative_to(opts.root) or path.suffix.lower() not in AUDIO_EXT:
            log(f"SKIPPED  (not a track of the library)  {path}")
            stats["skipped"] += 1
            continue
        try:
            current = fields.read(path)
        except Exception as e:
            log(f"SKIPPED  (unreadable: {e})  {path}")
            stats["skipped"] += 1
            continue
        after = dict(current)
        for edit in edits:
            try:
                new = fields.check(edit.field, [edit.value])
            except fields.FieldError as e:
                log(f"ERROR    ({e})  {path}")
                stats["errors"] += 1
                continue
            if edit.field == "genre" and new and mapping:
                validated = genres.resolve(new, mapping)
                if validated is None:
                    log(f"UNKNOWN  genre \"{new[0]}\" is not in the validated list  {path}")
                new = [validated] if validated else new
            if edit.seen is not None and current[edit.field] != [v.strip() for v in edit.seen if v.strip()]:
                log(f"REFUSED  ({edit.field} changed since: \"{shown(current[edit.field])}\")  {path}")
                stats["refused"] += 1
                continue
            if new == current[edit.field]:
                stats["unchanged"] += 1
                continue
            log(f"TAG      {edit.field}: \"{shown(current[edit.field])}\" → \"{shown(new)}\"  {path}")
            writes.append((path, edit.field, current[edit.field], new))
            after[edit.field] = new
        planned[path] = filing_tags(after)
    return writes, planned


def tag_pass(opts: Options, log):
    """A tag pass, without taking the lock: the caller holds it when `opts.apply`."""
    started = time.time()
    log(f"[{'APPLY' if opts.apply else 'DRY-RUN'}] tagging under {opts.root}")
    stats = defaultdict(int)
    writes, planned = plan(opts, log, stats)
    journal = Journal(opts.state_dir, dict(root=str(opts.root), started=started, argv=sys.argv[1:]))
    stopped = False
    if opts.apply:
        for i, (path, name, old, new) in enumerate(writes):
            if STOP.is_set():
                log(f"STOP     requested: {len(writes) - i} tag(s) not written, nor any track filed")
                stopped = True
                break
            try:
                fields.write(path, name, new)
            except Exception as e:
                log(f"ERROR    ({e})  {path}")
                stats["errors"] += 1
                continue
            journal.tag(path, name, old, new)
            stats["written"] += 1

    report = None
    if opts.refile and writes and not stopped:
        log("")
        report = organize.organize_pass(organize.Options(
            root=opts.root, paths=sorted({path.parent for path, *_ in writes}), apply=opts.apply,
            state_dir=opts.state_dir, lyrion=opts.lyrion, interruptible=False,
            overrides={} if opts.apply else planned), log, journal)
    journal.close()

    stats["tags to write"] = len(writes)
    if report:
        stats["moved" if opts.apply else "to move"] = len(report.done if opts.apply else report.moves)
    log("\n--- Tag summary ---")
    for k in ("tracks", "tags to write", "written", "unchanged", "to move", "moved", "refused",
              "skipped", "errors"):
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


def setting(s):
    """'FIELD=VALUE' -> (field, value)."""
    name, sep, value = s.partition("=")
    if not sep or name not in EDITABLE:
        raise argparse.ArgumentTypeError(f"expected FIELD=VALUE, FIELD among {', '.join(EDITABLE)}: {s!r}")
    try:
        fields.check(name, [value])
    except fields.FieldError as e:
        raise argparse.ArgumentTypeError(str(e)) from None
    return name, value


def main(argv=None):
    ap = argparse.ArgumentParser(description="Rewrite tag fields of tracks, then file them again.")
    root_env = os.environ.get("MUSIC_LIBRARIAN_ROOT")
    ap.add_argument("--root", default=root_env, required=not root_env,
                    help="library root (default: $MUSIC_LIBRARIAN_ROOT)")
    ap.add_argument("paths", nargs="+", help="tracks or folders of the library")
    ap.add_argument("--set", dest="settings", type=setting, action="append", required=True,
                    metavar="FIELD=VALUE", help="the value to write (empty: remove the field)")
    ap.add_argument("--no-refile", action="store_true", help="leave the edited tracks where they are")
    ap.add_argument("--apply", action="store_true", help="write the tags (otherwise dry run)")
    ap.add_argument("--state-dir", default=os.environ.get("MUSIC_LIBRARIAN_STATE"))
    ap.add_argument("--syslog", action="store_true",
                    help="also send the lines to syslog/journald (tag music-librarian)")
    lyrion.add_arguments(ap)
    args = ap.parse_args(argv)
    opts = Options(root=args.root, apply=args.apply, state_dir=state_path(args.state_dir),
                   refile=not args.no_refile,
                   edits=[Edit(p, name, value) for p in tracks(args.paths) for name, value in args.settings])
    opts.lyrion = lyrion.from_args(args, opts.root, opts.state_dir)
    log = Log(args.syslog)
    return recorded(lambda: run(opts, log), log, opts.state_dir, "tag", opts.apply)


if __name__ == "__main__":
    handle_stop_signals()
    sys.exit(main())
