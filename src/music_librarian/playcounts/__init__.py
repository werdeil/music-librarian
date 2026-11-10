"""
playcounts — gives the play counts Lyrion left on files gone back to the tracks of the library.

Lyrion keys its history on each file's URL (see integrations/lyrion.py). A file renamed outside a
pass leaves an orphan: an entry of tracks_persistent with plays or a rating, and none in the
Alternative Play Count plugin's table. Each orphan is given to a track:
  - the one a journal says its file became, at once;
  - the one picked for it with --review or on the dashboard, among the tracks whose path looks
    most like its own (see playcounts/matching.py), kept in state/playcount-matches.json;
  - none while it awaits an answer, or once ignored.
Its plays are added to the track's, the latest play and the rating kept as in a carry-over.

The pass also gives the entries whose urlmd5 is not the MD5 of their URL the right one, so Lyrion
and the plugin find them again (--no-rekey to skip it), copies the plugin's plays to the tracks
Lyrion counts none for (--no-backfill to skip it) and, with --dpsv, computes the plugin's DPSV
again from the counts of every track.

  music-librarian playcounts             # dry run: what would be written, the orphans awaiting
  music-librarian playcounts --review    # answer the orphans (writes the matches file only)
  music-librarian playcounts --apply     # write to persist.db, after a backup copy

Lyrion can stay up, as for a carry-over. The writes are not journaled: the backup copy in
state/lyrion-backups is the way back.

Exit codes: 0 = OK, 2 = a match skipped or the database unreadable, 3 = another pass is running.
"""
import contextlib
import dataclasses
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

from music_librarian.core import arguments
from music_librarian.core.files import AUDIO_EXT, is_junk
from music_librarian.core.lock import handle_stop_signals
from music_librarian.core.log import Log, count
from music_librarian.core.runs import recorded
from music_librarian.core.state import state_path
from music_librarian.integrations import lyrion
from music_librarian.integrations.lyrion import Lyrion
from music_librarian.organize.scan import collect
from music_librarian.playcounts import decisions, history, journals
from music_librarian.playcounts.matching import Matcher
from music_librarian.playcounts.review import review
from music_librarian.split.library import run_locked, run_review

SUMMARY = ("orphans", "matched", "from journals", "awaiting review", "ignored", "rekeyed", "backfilled",
           "DPSV computed", "skipped")


@dataclasses.dataclass
class Options:
    """A playcounts pass; `matches` defaults to the state folder's file."""
    root: Path
    lyrion: Lyrion
    apply: bool = False
    state_dir: Path = dataclasses.field(default_factory=state_path)
    backfill: bool = True
    dpsv: bool = False
    rekey: bool = True
    matches: Path | None = None

    def __post_init__(self):
        self.root = Path(self.root).resolve()
        self.matches = self.matches or self.state_dir / decisions.FILE


def main(argv=None):
    ap = arguments.parser("playcounts", "Give the play counts Lyrion left on files gone back to "
                                        "the library's tracks.")
    arguments.add_root(ap)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="write to persist.db (otherwise dry run)")
    mode.add_argument("--review", action="store_true",
                      help="answer the orphans, one at a time (writes the matches file only)")
    ap.add_argument("--no-rekey", action="store_true",
                    help="leave the entries whose urlmd5 is not the MD5 of their URL")
    ap.add_argument("--no-backfill", action="store_true",
                    help="leave the tracks Lyrion counts no play for, when the plugin counts some")
    ap.add_argument("--dpsv", action="store_true",
                    help="compute the plugin's DPSV again from the counts of every track")
    ap.add_argument("--matches", type=Path, help=f"matches file (default: state/{decisions.FILE})")
    lyrion.add_arguments(ap)
    arguments.add_state_dir(ap)
    arguments.add_syslog(ap)
    args = ap.parse_args(argv)
    state_dir = state_path(args.state_dir)
    root = Path(args.root).resolve()
    database = lyrion.from_args(args, root, state_dir)
    if database is None:
        raise SystemExit("playcounts needs --lyrion-db (or $LYRION_PERSIST_DB)")
    opts = Options(root=root, lyrion=database, apply=args.apply, state_dir=state_dir,
                   backfill=not args.no_backfill, dpsv=args.dpsv, rekey=not args.no_rekey,
                   matches=args.matches)
    log = Log(args.syslog)
    if args.review:
        return run_review(review_pass, opts, log)
    return recorded(lambda: run(opts, log), log, opts.state_dir, "playcounts", opts.apply)


def run(opts: Options, log):
    """A playcounts pass, under the lock with `apply`."""
    return run_locked(playcounts_pass, opts, log)


def tracks(opts: Options):
    """{urlmd5 Lyrion gives a track of the library: its path relative to the root}."""
    found = {}
    for path in collect([opts.root]):
        if not is_junk(path) and path.suffix.lower() in AUDIO_EXT:
            found[lyrion.md5(opts.lyrion.url(path))] = str(path.relative_to(opts.root))
    return found


def orphans_of(opts: Options, con, library):
    """[(orphan whose file is gone, the track a journal's moves lead its file to or None)], for a
    `library` as tracks() gives it."""
    moved = {lyrion.md5(url): dst for src, dst in journals.destinations(opts.state_dir).items()
             if (url := opts.lyrion.url(src))}
    found = []
    for orphan in history.orphans(con):
        if orphan.key not in library:
            url = opts.lyrion.url(moved.get(orphan.key, ""))
            found.append((orphan, library.get(lyrion.md5(url)) if url else None))
    return found


def sorted_out(orphans, matches: Path, filed):
    """The orphans of orphans_of() sorted by what settles them, `filed` being the library's
    relative paths: {"journal" | "matched" | "gone": [(orphan, its track)], "ignored" | "waiting":
    [orphan]}; "gone" holds the matches whose track left the library."""
    matched, ignored = decisions.load(matches)
    out = defaultdict(list)
    for orphan, moved in orphans:
        if orphan.key in matched:
            rel = matched[orphan.key]
            out["matched" if rel in filed else "gone"].append((orphan, rel))
        elif orphan.key in ignored:
            out["ignored"].append(orphan)
        elif moved:
            out["journal"].append((orphan, moved))
        else:
            out["waiting"].append(orphan)
    return out


def matcher(con, library):
    plays = history.play_counts(con)
    return Matcher([(key, rel, plays.get(key, 0)) for key, rel in library.items()])


def review_pass(opts: Options, log):
    log(f"reading the orphans of {opts.lyrion.db}…")
    library = tracks(opts)
    with contextlib.closing(opts.lyrion.connect()) as con:
        waiting = sorted_out(orphans_of(opts, con, library), opts.matches, set(library.values()))["waiting"]
        suggest = matcher(con, library)
    queue = [(orphan, opts.lyrion.relative(orphan.url)) for orphan in waiting]
    answered = review(queue, suggest.best, opts.matches, input, log)

    log("\n--- Review summary ---")
    log(f"{'answered':>20}: {answered}")
    log(f"{'orphans left':>20}: {len(queue) - answered}")
    log(f"{'matches':>20}: {opts.matches}")
    log(f"{'to write them':>20}: music-librarian playcounts --apply")
    return 0


def playcounts_pass(opts: Options, log):
    """A playcounts pass, without taking the lock: the caller holds it when `opts.apply`."""
    log(f"[{'APPLY' if opts.apply else 'DRY-RUN'}] play counts of {opts.lyrion.db}")
    stats = defaultdict(int)
    backup = None
    try:
        with contextlib.closing(opts.lyrion.connect()) as con:
            given = plan(opts, con, log, stats)
            miskeyed = history.miskeyed(con) if opts.rekey else []
            zeroed = history.zeroed(con) if opts.backfill else []
            changes = history.dpsv_changes(con) if opts.dpsv else []
        for table, row in miskeyed:
            log(f"REKEY    {opts.lyrion.relative(row['url'])}  ({table})")
        for url, plays in zeroed:
            log(f"BACKFILL {opts.lyrion.relative(url)}  ({count(plays, 'play')})")
        for _, url, kept, value in changes:
            log(f"DPSV     {opts.lyrion.relative(url)}  ({kept} → {value})")
        stats["backfilled"], stats["DPSV computed"] = len(zeroed), len(changes)
        stats["rekeyed"] = len(miskeyed)
        if opts.apply and (given or miskeyed or zeroed or changes):
            backup = opts.lyrion.backup()
            with opts.lyrion.writing() as con:
                if opts.rekey:
                    history.rekey(con)
                for orphan, rel in given:
                    history.adopt(con, orphan, opts.lyrion.url(opts.root / rel))
                if opts.backfill:
                    history.backfill(con)
                if opts.dpsv:
                    history.write_dpsv(con, history.dpsv_changes(con))
            decisions.update(opts.matches, drop=[orphan.key for orphan, _ in given])
    except sqlite3.Error as e:
        log(f"ERROR    {opts.lyrion.db}: {e}")
        return 2

    log("\n--- Playcounts summary ---")
    for k in SUMMARY:
        if stats[k]:
            log(f"{k:>20}: {stats[k]}")
    if backup:
        log(f"{'backup':>20}: {backup}")
    if stats["awaiting review"]:
        log(f"{'review':>20}: music-librarian playcounts --review")
    return 2 if stats["skipped"] else 0


def plan(opts: Options, con, log, stats):
    """Log what becomes of each orphan; return [(orphan, its track's relative path)] of those
    a journal or a match settles."""
    library = tracks(opts)
    found = sorted_out(orphans_of(opts, con, library), opts.matches, set(library.values()))
    for kind, tag in (("journal", "JOURNAL "), ("matched", "MATCH   ")):
        for orphan, rel in found[kind]:
            log(f"{tag} {opts.lyrion.relative(orphan.url)}  ({count(orphan.plays, 'play')})\n"
                f"         → {rel}")
    for orphan, rel in found["gone"]:
        log(f"SKIPPED  (its track is gone)  {opts.lyrion.relative(orphan.url)}\n         → {rel}")
    if not opts.apply and found["waiting"]:
        suggest = matcher(con, library)
        for orphan in found["waiting"]:
            path = opts.lyrion.relative(orphan.url)
            log(f"WAITING  {path}  ({count(orphan.plays, 'play')})")
            for note, _, rel, _ in suggest.best(path, orphan.plays, top=1):
                log(f"         → {rel}  ({note:.0f} %)")
    stats["orphans"] = sum(len(orphans) for orphans in found.values())
    stats["from journals"], stats["matched"] = len(found["journal"]), len(found["matched"])
    stats["awaiting review"], stats["ignored"] = len(found["waiting"]), len(found["ignored"])
    stats["skipped"] = len(found["gone"])
    return found["journal"] + found["matched"]


if __name__ == "__main__":
    handle_stop_signals()
    sys.exit(main())
