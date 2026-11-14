"""
organize — files a music library the way Apple Music does:
    <Album Artist>/<Album>/<NN Title>.ext
    Compilations/<Album>/<NN Title>.ext            (compilation flag)
    .../<D-NN Title>.ext                           (multi-disc album)

Dry run by default. --apply to actually do it.

Examples:
  # Check the whole library (nothing is changed)
  music-librarian organize --root /srv/music

  # File an album dropped in the Inbox
  music-librarian organize --root /srv/music /srv/inbox/MyAlbum --apply

  # Only process the folders changed since the last pass
  music-librarian organize --root /srv/music --since --apply

  # Undo a pass (dry run, then --apply)
  music-librarian undo state/journal/<date>.jsonl --root /srv/music

With --lyrion-db, the Lyrion history (play counts, ratings) follows moved files
(see integrations/lyrion.py). --lyrion-replay JOURNAL carries it over afterwards for one pass.
With --lyrion-url too, an applied pass waits for the end of Lyrion's scan before its moves and
before the carry-over (--scan-poll, --scan-timeout, --no-wait-scan).

State (move journals, last pass date): --state-dir, else
$MUSIC_LIBRARIAN_STATE, else $XDG_STATE_HOME/music-librarian
(~/.local/state/music-librarian).

--root can also come from $MUSIC_LIBRARIAN_ROOT.

One --apply pass at a time (state/lock). On SIGTERM or Ctrl-C, the current
move completes and the rest is left for the next pass (second Ctrl-C: stop
at once).

A track whose title or album holds values that differ or a ";" is SKIPPED (see repeats); other
fields whose values differ (an artist spelled two ways) are reported as MULTIPLE.

Exit codes: 0 = OK, 2 = conflicts, skipped files, MULTIPLE, failed rewrites or refused undos,
3 = another pass is running, 4 = stopped before the end, 5 = Lyrion carry-over failed.
"""
import argparse
import contextlib
import shlex
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from music_librarian import repeats
from music_librarian.core import arguments
from music_librarian.core.files import AUDIO_EXT, is_junk, prune_empty, relative
from music_librarian.core.journal import Journal, move_file
from music_librarian.core.lock import EXIT_LOCKED, EXIT_STOPPED, STOP, Locked, handle_stop_signals, locked
from music_librarian.core.log import Log, count
from music_librarian.core.runs import recorded
from music_librarian.core.state import read_stamp, state_path, write_stamp
from music_librarian.integrations import lyrion
from music_librarian.integrations.lyrion import Lyrion
from music_librarian.media import fields
from music_librarian.media.atoms import MP4_EXT, leading_mdat
from music_librarian.media.faststart import RewriteError, faststart
from music_librarian.organize.naming import is_in_place, nfc, same_component, target_rel
from music_librarian.media.metadata_cache import MetadataCache
from music_librarian.organize.scan import collect, read_tags, tags_from_snapshot
from music_librarian.organize.undo import undo

LAST_RUN = "last_run"  # --since state file
# Taken off the start time stored in last_run: the kernel stamps ctime with a
# coarser clock than time.time(), a few ms behind, so a file changed right after
# the start would look older and the next --since would miss it. At worst
# those folders are read again.
SINCE_MARGIN = 2.0


@dataclass
class Options:
    """A filing pass. `paths` defaults to the whole root; with `interruptible`
    False a stop request does not cut the moves short (import files an item entirely or not at all).
    `overrides` ({path: tags as read_tags returns them}) files those files by these tags instead
    of theirs, for a dry run of tags not written yet."""
    root: Path
    paths: list = field(default_factory=list)
    apply: bool = False
    compilations_dir: str = "Compilations"
    strict_case: bool = False
    since: bool = False
    state_dir: Path = field(default_factory=state_path)
    lyrion: Lyrion | None = None
    interruptible: bool = True
    overrides: dict = field(default_factory=dict)

    def __post_init__(self):
        self.root = Path(self.root).resolve()
        self.paths = [Path(p).resolve() for p in self.paths]


@dataclass
class Report:
    """`moves` lists the planned moves, `done` the ones carried out, `multiple` the fields
    whose values differ (see repeats), which leave `code` alone."""
    code: int
    moves: list = field(default_factory=list)
    done: list = field(default_factory=list)
    journal: Path | None = None
    multiple: int = 0


def last_run(state_dir: Path):
    return read_stamp(state_dir, LAST_RUN)


def parser():
    ap = arguments.parser("organize", "File the music library the way Apple Music does.")
    arguments.add_root(ap)
    ap.add_argument("paths", nargs="*", help="folders to process (default: --root)")
    arguments.add_apply(ap)
    ap.add_argument("--compilations-dir", default="Compilations",
                    help="folder of the compilations, under the root (default: Compilations)")
    ap.add_argument("--strict-case", action="store_true",
                    help="also rename case-only differences")
    ap.add_argument("--since", action="store_true",
                    help="only process the folders changed since the last pass")
    ap.add_argument("--undo", metavar="JOURNAL", help=argparse.SUPPRESS)
    ap.add_argument("--lyrion-replay", metavar="JOURNAL",
                    help="carry the Lyrion history over for the moves of a journal")
    lyrion.add_arguments(ap)
    arguments.add_state_dir(ap)
    arguments.add_syslog(ap)
    return ap


def undo_parser():
    ap = arguments.parser("undo", "Undo a pass: its moves and tag changes, in reverse.")
    ap.add_argument("undo", metavar="JOURNAL", help="the journal the pass's summary names")
    arguments.add_root(ap)
    arguments.add_apply(ap)
    lyrion.add_arguments(ap)
    arguments.add_state_dir(ap)
    arguments.add_syslog(ap)
    ap.set_defaults(paths=[], compilations_dir="Compilations", strict_case=False, since=False,
                    lyrion_replay=None)
    return ap


def main(argv=None, ap=None):
    args = (ap or parser()).parse_args(argv)
    log = Log(args.syslog)
    opts = Options(root=args.root, paths=args.paths, apply=args.apply,
                   compilations_dir=args.compilations_dir, strict_case=args.strict_case,
                   since=args.since, state_dir=state_path(args.state_dir))
    opts.lyrion = lyrion.from_args(args, opts.root, opts.state_dir)
    if not (args.undo or args.lyrion_replay):
        return recorded(lambda: run(opts, log).code, log, opts.state_dir, "organize", opts.apply)
    command = "lyrion-replay" if args.lyrion_replay else "undo"
    return recorded(lambda: replay(args, opts, log), log, opts.state_dir, command, opts.apply)


def undo_main(argv=None):
    return main(argv, undo_parser())


def replay(args, opts: Options, log):
    """--undo or --lyrion-replay, under the lock with `apply`."""
    try:
        with locked(opts.state_dir) if opts.apply else contextlib.nullcontext():
            if args.lyrion_replay:
                return lyrion.replay(args, opts.lyrion, log)
            return undo(args, opts.root, opts.state_dir, log, opts.lyrion)
    except Locked as e:
        log(f"LOCKED   another pass is running ({e}), giving up")
        return EXIT_LOCKED


def run(opts: Options, log):
    """A filing pass, under the lock with `apply`."""
    try:
        with locked(opts.state_dir) if opts.apply else contextlib.nullcontext():
            report = organize_pass(opts, log)
    except Locked as e:
        log(f"LOCKED   another pass is running ({e}), giving up")
        return Report(EXIT_LOCKED)
    if report.code == 0 and report.multiple:
        report.code = 2
    return report


def values_to_decide(path: Path, root, cache):
    """repeats.to_decide() of the file, through the metadata cache for a file of the library."""
    if not path.is_relative_to(root):
        try:
            return repeats.to_decide(fields.read(path))
        except Exception:
            return []
    return repeats.to_decide(repeats.from_snapshot(cache.snapshot(path)) or {})


def file_tags(path: Path, root, cache):
    """read_tags(path), through the metadata cache for a file of the library; raises for an
    unreadable one. Files outside the library (the Inbox) are read directly, never cached."""
    if not path.is_relative_to(root):
        return read_tags(path)
    snap = cache.snapshot(path)
    if snap is None:
        raise ValueError("no readable tags")
    return tags_from_snapshot(snap)


def needs_faststart(path: Path, root, cache):
    """True for an MP4 file whose moov atom comes after mdat, through the metadata cache for a
    file of the library."""
    if path.suffix.lower() not in MP4_EXT:
        return False
    if not path.is_relative_to(root):
        return leading_mdat(path)
    return bool((cache.snapshot(path) or {}).get("leading_mdat"))


def exact_folder_elsewhere(root, cur_rel, tgt_rel):
    """True when a folder of the target path exists as written and is not the file's own folder
    at that depth: a tolerated variant (case, NFD, truncation) then joins it."""
    for depth in range(1, len(tgt_rel.parts)):
        want = root.joinpath(*tgt_rel.parts[:depth])
        have = root.joinpath(*cur_rel.parts[:depth])
        if want != have and want.is_dir() and not want.samefile(have):
            return True
    return False


class Folders:
    """The folders a pass files into. A target folder missing as written joins an existing or
    planned one it only differs from by case, normalization or truncation."""

    def __init__(self, root: Path, strict_case):
        self.root, self.strict_case = root, strict_case
        self.children = {}

    def names_in(self, folder: Path):
        if folder not in self.children:
            try:
                self.children[folder] = {p.name for p in folder.iterdir() if p.is_dir()}
            except OSError:
                self.children[folder] = set()
        return self.children[folder]

    def resolve(self, tgt_rel: Path):
        folder = self.root
        for part in tgt_rel.parts[:-1]:
            names = self.names_in(folder)
            if part not in names:
                variants = sorted(n for n in names if same_component(n, part, self.strict_case))
                part = variants[0] if variants else part
                names.add(part)
            folder = folder / part
        return folder.relative_to(self.root) / tgt_rel.name


@dataclass
class Plan:
    """What a pass found to do, and its counts. `blocked` holds the folders whose side files
    must not move: a track of theirs stays behind."""
    stats: defaultdict = field(default_factory=lambda: defaultdict(int))
    entries: list = field(default_factory=list)   # (file, tags, relative target path)
    junk: list = field(default_factory=list)
    rewrites: list = field(default_factory=list)
    moves: list = field(default_factory=list)     # (src, dst) of the tracks
    extra: list = field(default_factory=list)     # (src, dst) of the side files
    blocked: set = field(default_factory=set)

    def skip(self, p: Path, why, root, log):
        log(f"SKIPPED  ({why})  {relative(p, root)}")
        self.stats["skipped"] += 1
        self.blocked.add(p.parent)


def organize_pass(opts: Options, log, journal=None):
    """A filing pass, without taking the lock: the caller holds it when `opts.apply`. Its moves
    go to `journal` when given, which the caller then closes, else to a journal of its own."""
    started = time.time()
    scan = opts.paths or [opts.root]
    log(f"[{'APPLY' if opts.apply else 'DRY-RUN'}] root: {opts.root}")
    plan = Plan()
    read_entries(opts, scan, since_last_run(opts, log), plan, log)
    dir_targets = plan_moves(opts, mixed_compilations(opts.root, plan, log), plan, log)
    plan_side_files(opts.root, dir_targets, plan, log)
    for p in plan.rewrites:
        log(f"REWRITE  (moov after mdat)  {relative(p, opts.root)}")
    for src, dst in plan.moves + plan.extra:
        log(f"MOVE     {relative(src, opts.root)}\n         → {relative(dst, opts.root)}")

    own_journal = journal is None
    if own_journal:
        journal = Journal(opts.state_dir, dict(root=str(opts.root), started=started, argv=sys.argv[1:]))
    lyrion.await_scan(opts.lyrion, opts.apply and bool(plan.rewrites or plan.moves or plan.extra), log)
    done, stopped = apply_plan(opts, scan, started, plan, journal, log) if opts.apply else ([], False)
    if own_journal:
        journal.close()
    lyrion_ok = summary(opts, plan, done, journal if own_journal else None, log)

    stats = plan.stats
    code = EXIT_STOPPED if stopped else 2 if stats["conflicts"] or stats["skipped"] or stats["errors"] else 0
    if code == 0 and not lyrion_ok:
        code = lyrion.EXIT_LYRION
    return Report(code, plan.moves + plan.extra, done, journal.path, stats["multiple values"])


def since_last_run(opts: Options, log):
    """The time --since reads the folders changed after, None for every folder."""
    if not opts.since:
        return None
    since = last_run(opts.state_dir)
    if since is None:
        log("no known last pass: everything is processed")
    else:
        log(f"since {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(since))}")
    return since


def read_entries(opts: Options, scan, since, plan: Plan, log):
    """Read the tags of every music file under `scan` into plan.entries, with its target; the
    junk files, the MP4 files to rewrite and the files skipped go to the plan too."""
    root = opts.root
    with MetadataCache(opts.state_dir) as cache:
        for p in collect(scan, since):
            if is_junk(p):
                plan.junk.append(p)
                continue
            if p.suffix.lower() not in AUDIO_EXT:
                continue
            plan.stats["scanned"] += 1
            try:
                tags = opts.overrides.get(p) or file_tags(p, root, cache)
            except Exception as e:
                plan.skip(p, f"unreadable: {e}", root, log)
                continue
            refused = None
            for name, values in [] if p in opts.overrides else values_to_decide(p, root, cache):
                if name in repeats.SINGLE:
                    refused = refused or f"{repeats.refusal(name, values)}: {'; '.join(values)!r}"
                else:
                    log(repeats.multiple_line(p, root, name, values))
                    plan.stats["multiple values"] += 1
            if refused:
                plan.skip(p, refused, root, log)
                continue
            if needs_faststart(p, root, cache):
                plan.rewrites.append(p)
            tgt = target_rel(tags, p.suffix, opts.compilations_dir) if tags else None
            if tgt is None:
                plan.skip(p, "not enough tags", root, log)
                continue
            plan.entries.append((p, tags, tgt))


def mixed_compilations(root, plan: Plan, log):
    """The files of an album (same folder, same album tag) whose tracks differ on the
    compilation flag: moved, it would be split between Compilations/ and an artist folder."""
    groups = defaultdict(list)
    for e in plan.entries:
        groups[(e[0].parent, nfc(e[1]["album"] or "").casefold())].append(e)
    mixed = set()
    for (d, _), grp in groups.items():
        n = sum(e[1]["compilation"] for e in grp)
        if 0 < n < len(grp):
            log(f"CONFLICT (inconsistent cpil: {n}/{len(grp)} tracks flagged compilation)"
                f"  {relative(d, root)}  [{grp[0][1]['album']}]")
            plan.stats["conflicts"] += len(grp)
            mixed.update(e[0] for e in grp)
            plan.blocked.add(d)
    return mixed


def plan_moves(opts: Options, mixed, plan: Plan, log):
    """Fill plan.moves with the tracks not in place, a target already taken left as a conflict;
    return {source folder: its tracks' target folders}."""
    root = opts.root
    seen_targets = {}               # dst -> src
    dir_targets = defaultdict(set)
    folders = Folders(root, opts.strict_case)
    for p, _, tgt in plan.entries:
        if p in mixed:
            continue
        cur_rel = p.relative_to(root) if p.is_relative_to(root) else None   # None: the Inbox
        if cur_rel is not None and is_in_place(cur_rel, tgt, opts.strict_case) \
                and not exact_folder_elsewhere(root, cur_rel, tgt):
            plan.stats["in place"] += 1
            dir_targets[p.parent].add(p.parent)
            continue
        dst = root / folders.resolve(tgt)
        key = nfc(str(dst)).casefold()
        if key in seen_targets or (dst.exists() and not dst.samefile(p)):
            other = seen_targets.get(key, dst)
            log(f"CONFLICT {relative(p, root)}\n         → {relative(dst, root)}\n"
                f"         (already taken by {relative(other, root)})")
            plan.stats["conflicts"] += 1
            plan.blocked.add(p.parent)
            continue
        seen_targets[key] = p
        plan.moves.append((p, dst))
        dir_targets[p.parent].add(dst.parent)
    return dir_targets


def plan_side_files(root, dir_targets, plan: Plan, log):
    """Fill plan.extra with the side files (folder.jpg, .lrc…) of the folders whose tracks all go
    to one other folder, none left behind. One whose name is taken there gets a free one."""
    taken = set()
    for src_dir, tdirs in dir_targets.items():
        if len(tdirs) != 1 or src_dir in plan.blocked:
            continue
        tdir = next(iter(tdirs))
        if tdir == src_dir:
            continue
        for p in sorted(src_dir.iterdir()):
            if p.is_file() and not is_junk(p) and p.suffix.lower() not in AUDIO_EXT:
                dst = free_name(tdir / p.name, taken)
                if dst.name != p.name:
                    log(f"RENAME   (side file already there)  {relative(tdir / p.name, root)}"
                        f"\n         → {dst.name}")
                    plan.stats["side files renamed"] += 1
                taken.add(nfc(str(dst)).casefold())
                plan.extra.append((p, dst))


def free_name(dst: Path, taken):
    """`dst`, or `name (2).ext`, `name (3).ext`… when it exists or another side file takes it."""
    candidate, n = dst, 1
    while candidate.exists() or nfc(str(candidate)).casefold() in taken:
        n += 1
        candidate = dst.with_name(f"{dst.stem} ({n}){dst.suffix}")
    return candidate


def apply_plan(opts: Options, scan, started, plan: Plan, journal, log):
    """Rewrite, move through `journal`, delete the junk and prune the emptied folders; return
    (the moves done, whether a stop cut them short)."""
    root, stats = opts.root, plan.stats
    todo = plan.moves + plan.extra
    done, touched = [], set()
    stopped = rewrite_all(plan.rewrites, root, opts.interruptible, stats, log)
    for i, (src, dst) in enumerate([] if stopped else todo):
        if opts.interruptible and STOP.is_set():
            log(f"STOP     requested: {count(len(todo) - i, 'move')} not done, left for the next pass")
            stopped = True
            break
        if dst.exists() and not dst.samefile(src):
            log(f"CONFLICT (appeared during the pass)  {relative(dst, root)}")
            stats["conflicts"] += 1
            continue
        move_file(src, dst, journal)
        done.append((src, dst))
        stats["moved"] += 1
        touched.add(src.parent)
    for j in plan.junk:
        j.unlink(missing_ok=True)
        touched.add(j.parent)
    protected = {root, *scan}       # never pruned, even emptied
    for d in sorted(touched, key=lambda d: -len(d.parts)):
        stats["folders removed"] += prune_empty(d, protected)
    # Only a whole pass moves last_run forward; the conflicts left come back on the next one.
    if scan == [root] and not stopped:
        write_stamp(opts.state_dir, LAST_RUN, started - SINCE_MARGIN)
    return done, stopped


def summary(opts: Options, plan: Plan, done, journal, log):
    """Log the summary, after carrying the Lyrion history over; `journal` is the pass's own one,
    None when the caller's. Return whether the carry-over succeeded."""
    stats = plan.stats
    stats["to rewrite"] = len(plan.rewrites)
    stats["to move"] = len(plan.moves)
    stats["side files to move"] = len(plan.extra)
    stats["junk files"] = len(plan.junk)
    log("\n--- Organize summary ---")
    for k in ("scanned", "in place", "to rewrite", "rewritten", "to move", "side files to move",
              "side files renamed", "moved", "skipped", "conflicts", "multiple values", "errors",
              "junk files", "folders removed"):
        if stats[k]:
            log(f"{k:>20}: {stats[k]}")
    lyrion_ok = lyrion.carry_over(opts.lyrion, done if opts.apply else plan.moves, opts.apply, log)
    if journal and journal.path:
        log(f"{'journal':>20}: {journal.path}")
        log(f"{'undo':>20}: music-librarian undo {shlex.quote(str(journal.path))} "
            f"--root {shlex.quote(str(opts.root))} --apply")
        if not lyrion_ok:
            log(f"{'Lyrion retry':>20}: music-librarian organize --lyrion-replay "
                f"{shlex.quote(str(journal.path))} --apply")
    return lyrion_ok


def rewrite_all(rewrites, root, interruptible, stats, log):
    """Puts moov ahead of mdat in place, before the moves. True when stopped."""
    for i, p in enumerate(rewrites):
        if interruptible and STOP.is_set():
            log(f"STOP     requested: {count(len(rewrites) - i, 'rewrite')} and the moves not done,"
                " left for the next pass")
            return True
        try:
            faststart(p)
            stats["rewritten"] += 1
        except (RewriteError, OSError, subprocess.CalledProcessError) as e:
            log(f"ERROR    {e}  {relative(p, root)}")
            stats["errors"] += 1
    return False


if __name__ == "__main__":
    handle_stop_signals()
    sys.exit(main())
