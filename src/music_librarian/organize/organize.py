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

State (move journals, last pass date): --state-dir, else
$MUSIC_LIBRARIAN_STATE, else $XDG_STATE_HOME/music-librarian
(~/.local/state/music-librarian).

--root can also come from $MUSIC_LIBRARIAN_ROOT.

One --apply pass at a time (state/lock). On SIGTERM or Ctrl-C, the current
move completes and the rest is left for the next pass (second Ctrl-C: stop
at once).

Exit codes: 0 = OK, 2 = conflicts, skipped files, failed rewrites or refused undos,
3 = another pass is running, 4 = stopped before the end, 5 = Lyrion carry-over failed.
"""
import argparse
import contextlib
import os
import shlex
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from music_librarian.core.files import AUDIO_EXT, is_junk, prune_empty
from music_librarian.core.journal import Journal, move_file
from music_librarian.core.lock import EXIT_LOCKED, EXIT_STOPPED, STOP, Locked, handle_stop_signals, locked
from music_librarian.core.log import Log
from music_librarian.core.state import read_stamp, state_path, write_stamp
from music_librarian.integrations import lyrion
from music_librarian.integrations.lyrion import Lyrion
from music_librarian.media.atoms import MP4_EXT, leading_mdat
from music_librarian.media.faststart import RewriteError, faststart
from music_librarian.organize.naming import is_in_place, nfc, target_rel
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
    False a stop request does not cut the moves short (import files an item entirely or not at all)."""
    root: Path
    paths: list = field(default_factory=list)
    apply: bool = False
    compilations_dir: str = "Compilations"
    strict_case: bool = False
    since: bool = False
    state_dir: Path = field(default_factory=state_path)
    lyrion: Lyrion | None = None
    interruptible: bool = True

    def __post_init__(self):
        self.root = Path(self.root).resolve()
        self.paths = [Path(p).resolve() for p in self.paths]


@dataclass
class Report:
    """`moves` lists the planned moves, `done` the ones carried out."""
    code: int
    moves: list = field(default_factory=list)
    done: list = field(default_factory=list)
    journal: Path | None = None


def last_run(state_dir: Path):
    return read_stamp(state_dir, LAST_RUN)


def parser():
    ap = argparse.ArgumentParser(description="File the music library the way Apple Music does.")
    root_env = os.environ.get("MUSIC_LIBRARIAN_ROOT")
    ap.add_argument("--root", default=root_env, required=not root_env,
                    help="library root (default: $MUSIC_LIBRARIAN_ROOT)")
    ap.add_argument("paths", nargs="*", help="folders to process (default: --root)")
    ap.add_argument("--apply", action="store_true", help="do it (otherwise dry run)")
    ap.add_argument("--compilations-dir", default="Compilations")
    ap.add_argument("--strict-case", action="store_true",
                    help="also rename case-only differences")
    ap.add_argument("--since", action="store_true",
                    help="only process the folders changed since the last pass")
    ap.add_argument("--undo", metavar="JOURNAL", help="undo the moves of a journal")
    ap.add_argument("--lyrion-replay", metavar="JOURNAL",
                    help="carry the Lyrion history over for the moves of a journal")
    lyrion.add_arguments(ap)
    ap.add_argument("--state-dir", default=os.environ.get("MUSIC_LIBRARIAN_STATE"),
                    help="state folder (journals, last pass)")
    ap.add_argument("--syslog", action="store_true",
                    help="also send the lines to syslog/journald (tag music-librarian)")
    return ap


def main(argv=None):
    args = parser().parse_args(argv)
    log = Log(args.syslog)
    opts = Options(root=args.root, paths=args.paths, apply=args.apply,
                   compilations_dir=args.compilations_dir, strict_case=args.strict_case,
                   since=args.since, state_dir=state_path(args.state_dir))
    opts.lyrion = lyrion.from_args(args, opts.root, opts.state_dir)
    if not (args.undo or args.lyrion_replay):
        return run(opts, log).code
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
            return organize_pass(opts, log)
    except Locked as e:
        log(f"LOCKED   another pass is running ({e}), giving up")
        return Report(EXIT_LOCKED)


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


def organize_pass(opts: Options, log):
    """A filing pass, without taking the lock: the caller holds it when `opts.apply`."""
    started = time.time()
    root, state_dir = opts.root, opts.state_dir
    scan = opts.paths or [root]
    mode = "APPLY" if opts.apply else "DRY-RUN"
    log(f"[{mode}] root: {root}")

    since = None
    if opts.since:
        since = last_run(state_dir)
        if since is None:
            log("no known last pass: everything is processed")
        else:
            log(f"since {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(since))}")

    entries = []                  # (file, tags, relative target path)
    stats = defaultdict(int)
    junk = []
    rewrites = []
    blocked = set()               # folders whose side files must not move

    with MetadataCache(state_dir) as cache:
        for p in collect(scan, since):
            if is_junk(p):
                junk.append(p)
                continue
            if p.suffix.lower() not in AUDIO_EXT:
                continue
            stats["scanned"] += 1
            try:
                tags = file_tags(p, root, cache)
            except Exception as e:
                log(f"SKIPPED  (unreadable: {e})  {p}")
                stats["skipped"] += 1
                blocked.add(p.parent)
                continue
            if needs_faststart(p, root, cache):
                rewrites.append(p)
            tgt = target_rel(tags, p.suffix, opts.compilations_dir) if tags else None
            if tgt is None:
                log(f"SKIPPED  (not enough tags)  {p}")
                stats["skipped"] += 1
                blocked.add(p.parent)
                continue
            entries.append((p, tags, tgt))

    # Compilation safeguard: an album (same folder, same album tag) whose cpil
    # flag differs between tracks would be split between Compilations/ and an
    # artist folder, so it is left alone.
    groups = defaultdict(list)
    for e in entries:
        groups[(e[0].parent, nfc(e[1]["album"] or "").casefold())].append(e)
    mixed = set()
    for (d, _), grp in groups.items():
        n = sum(e[1]["compilation"] for e in grp)
        if 0 < n < len(grp):
            log(f"CONFLICT (inconsistent cpil: {n}/{len(grp)} tracks flagged compilation)"
                f"  {d}  [{grp[0][1]['album']}]")
            stats["conflicts"] += len(grp)
            mixed.update(e[0] for e in grp)
            blocked.add(d)

    moves = []                    # (src, dst)
    seen_targets = {}             # dst -> src
    dir_targets = defaultdict(set)  # source folder -> target folders

    for p, tags, tgt in entries:
        if p in mixed:
            continue
        try:
            cur_rel = p.relative_to(root)
        except ValueError:
            cur_rel = None  # file outside the library (Inbox)

        if cur_rel is not None and is_in_place(cur_rel, tgt, opts.strict_case) \
                and not exact_folder_elsewhere(root, cur_rel, tgt):
            stats["in place"] += 1
            dir_targets[p.parent].add(p.parent)
            continue

        dst = root / tgt
        key = nfc(str(dst)).casefold()
        if key in seen_targets or (dst.exists() and not dst.samefile(p)):
            other = seen_targets.get(key, dst)
            log(f"CONFLICT {p}\n         → {dst}\n         (already taken by {other})")
            stats["conflicts"] += 1
            blocked.add(p.parent)
            continue
        seen_targets[key] = p
        moves.append((p, dst))
        dir_targets[p.parent].add(dst.parent)

    # Side files (folder.jpg, .lrc…) follow the album if every track in the
    # folder goes to the same target folder and none stayed behind (conflict,
    # missing tags).
    extra = []
    for src_dir, tdirs in dir_targets.items():
        if len(tdirs) != 1 or src_dir in blocked:
            continue
        tdir = next(iter(tdirs))
        if tdir == src_dir:
            continue
        for p in sorted(src_dir.iterdir()):
            if p.is_file() and not is_junk(p) and p.suffix.lower() not in AUDIO_EXT:
                dst = tdir / p.name
                if dst.exists():
                    log(f"CONFLICT (side file already there)  {dst}")
                    stats["conflicts"] += 1
                else:
                    extra.append((p, dst))

    for p in rewrites:
        log(f"REWRITE  (moov after mdat)  {p}")
    for src, dst in moves + extra:
        log(f"MOVE     {src}\n         → {dst}")

    journal = Journal(state_dir, dict(root=str(root), started=started, argv=sys.argv[1:]))
    done = []
    stopped = False
    if opts.apply:
        stopped = rewrite_all(rewrites, opts.interruptible, stats, log)
        touched = set()
        for i, (src, dst) in enumerate([] if stopped else moves + extra):
            if opts.interruptible and STOP.is_set():
                log(f"STOP     requested: {len(moves) + len(extra) - i} move(s) not done,"
                    " left for the next pass")
                stopped = True
                break
            if dst.exists() and not dst.samefile(src):
                log(f"CONFLICT (appeared during the pass)  {dst}")
                stats["conflicts"] += 1
                continue
            move_file(src, dst, journal)
            done.append((src, dst))
            stats["moved"] += 1
            touched.add(src.parent)
        for j in junk:
            j.unlink(missing_ok=True)
            touched.add(j.parent)
        # emptied folders (never the root nor the folders passed as arguments)
        protected = {root, *scan}
        for d in sorted(touched, key=lambda d: -len(d.parts)):
            stats["folders removed"] += prune_empty(d, protected)
        # The last pass only moves forward after a pass over the whole root.
        # Remaining conflicts do not hold it back: they show up again on the
        # next full pass, or as soon as their folder is touched.
        if scan == [root] and not stopped:
            write_stamp(state_dir, LAST_RUN, started - SINCE_MARGIN)
    journal.close()

    stats["to rewrite"] = len(rewrites)
    stats["to move"] = len(moves)
    stats["side files to move"] = len(extra)
    stats["junk files"] = len(junk)
    log("\n--- Summary ---")
    for k in ("scanned", "in place", "to rewrite", "rewritten", "to move", "side files to move",
              "moved", "skipped", "conflicts", "errors", "junk files", "folders removed"):
        if stats[k]:
            log(f"{k:>20}: {stats[k]}")
    lyrion_ok = lyrion.carry_over(opts.lyrion, done if opts.apply else moves, opts.apply, log)
    if journal.path:
        log(f"{'journal':>20}: {journal.path}")
        log(f"{'undo':>20}: music-librarian undo {shlex.quote(str(journal.path))} "
            f"--root {shlex.quote(str(root))} --apply")
        if not lyrion_ok:
            log(f"{'Lyrion retry':>20}: music-librarian organize --lyrion-replay "
                f"{shlex.quote(str(journal.path))} --apply")

    code = EXIT_STOPPED if stopped else 2 if stats["conflicts"] or stats["skipped"] or stats["errors"] else 0
    if code == 0 and not lyrion_ok:
        code = lyrion.EXIT_LYRION
    return Report(code, moves + extra, done, journal.path)


def rewrite_all(rewrites, interruptible, stats, log):
    """Puts moov ahead of mdat in place, before the moves. True when stopped."""
    for i, p in enumerate(rewrites):
        if interruptible and STOP.is_set():
            log(f"STOP     requested: {len(rewrites) - i} rewrite(s) and the moves not done,"
                " left for the next pass")
            return True
        try:
            faststart(p)
            stats["rewritten"] += 1
        except (RewriteError, OSError, subprocess.CalledProcessError) as e:
            log(f"ERROR    {e}  {p}")
            stats["errors"] += 1
    return False


if __name__ == "__main__":
    handle_stop_signals()
    sys.exit(main())
