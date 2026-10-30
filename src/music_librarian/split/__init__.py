"""
split — splits the artist and composer credits that hold several names.

Lyrion splits a tag on ";", so "John Lennon/Paul McCartney" is rewritten
"John Lennon; Paul McCartney". A comma or a slash only splits a credit when the
library vouches for it:
  - a credit that is an album artist somewhere (AC/DC, Earth, Wind & Fire) is kept;
  - a credit whose fragments are all names seen on their own is split;
  - any other credit waits for a decision in state/split-decisions.txt, one line
    per credit: "CREDIT => split", "=> keep" or "=> Name A; Name B".
--apply writes the credits waiting for a decision at the end of that file, and
--review asks about them one at a time (see split/review.py). A decision vouches
for the names it gives, which can settle other credits.

The artist is only rewritten on tracks that have an album artist, which organize
files them by. A tag holding several values already is left alone.

  music-librarian split                    # dry run over the whole library
  music-librarian split --apply
  music-librarian split --apply PATH...    # only these folders
  music-librarian split --review           # answer the credits awaiting a decision

The known names are those of every file under the root in the metadata cache, so a
pass over a few folders (the one import runs) still knows the whole library.
Every tag change goes through the journal: `music-librarian undo` restores it.

Exit codes: 0 = OK, 2 = unreadable files or failed writes, 3 = another pass is
running, 4 = stopped before the end.
"""
import dataclasses
import sys
import time
from collections import defaultdict
from pathlib import Path

from music_librarian.core.journal import Journal
from music_librarian.core.lock import handle_stop_signals
from music_librarian.core.log import Log, count
from music_librarian.core.runs import recorded
from music_librarian.core.state import state_path
from music_librarian.media import credit_tags
from music_librarian.media.metadata_cache import MetadataCache
from music_librarian.split import decisions
from music_librarian.split.library import (CreditOptions, finish, parser, read_tracks, rewrite_tags,
                                           run_locked, run_review)
from music_librarian.split.review import review
from music_librarian.split.rules import PENDING, PROTECTED, decided_names, key, known_names, rewrite

FIELDS = ("composer", "artist")


@dataclasses.dataclass
class Options(CreditOptions):
    """A split pass; `decisions` defaults to the state folder's file."""
    decisions: Path | None = None

    def __post_init__(self):
        super().__post_init__()
        self.decisions = self.decisions or self.state_dir / decisions.FILE


def main(argv=None):
    ap = parser("split", "Split the credits that hold several names.",
                "answer the credits awaiting a decision, one at a time (writes the decisions file only)")
    ap.add_argument("--decisions", type=Path,
                    help=f"decisions file (default: state/{decisions.FILE})")
    args = ap.parse_args(argv)
    opts = Options(root=args.root, paths=args.paths, apply=args.apply,
                   state_dir=state_path(args.state_dir), decisions=args.decisions)
    log = Log(args.syslog)
    if args.review:
        return run_review(review_pass, opts, log)
    return recorded(lambda: run(opts, log), log, opts.state_dir, "split", opts.apply)


def run(opts: Options, log):
    """A split pass, under the lock with `apply`."""
    return run_locked(split_pass, opts, log)


def known_from(tracks):
    """The known names of these tracks' credits (credit_tags.read() dicts)."""
    return known_names([v for t in tracks for v in t["albumartist"]],
                       [v for t in tracks for f in FIELDS for v in t[f]])


def library_names(root, state_dir):
    """The known names of every file under `root`, as the metadata cache last read it."""
    with MetadataCache(state_dir) as cache:
        return known_from([c for _, snap in cache.cached(root) if (c := credit_tags.from_snapshot(snap))])


def plan(tracks, known, decided):
    """({(field, old, new): [files]}, {key: [credit, tracks, first file, status]} left whole,
    status being PENDING or PROTECTED)."""
    changes, pending = defaultdict(list), {}
    for p, tags in tracks:
        for field in FIELDS:
            values = tags[field]
            if len(values) != 1 or (field == "artist" and not tags["albumartist"]):
                continue
            new, waiting = rewrite(values[0], known, decided)
            for credit, status in waiting:
                pending.setdefault(key(credit), [credit, 0, p, status])[1] += 1
            if new:
                changes[(field, values[0], new)].append(p)
    return changes, pending


def by_frequency(pending, status):
    return sorted((c for c in pending.values() if c[3] == status), key=lambda c: (-c[1], key(c[0])))


def review_pass(opts: Options, log):
    root, state_dir, decisions_path, scan = opts.root, opts.state_dir, opts.decisions, opts.scan
    decided = decisions.load(decisions_path, log)
    stats = defaultdict(int)
    log(f"reading the credits of {', '.join(map(str, scan))}…")
    tracks = read_tracks(scan, state_dir, log, stats)
    known = library_names(root, state_dir)
    _, pending = plan(tracks, known | decided_names(decided), decided)
    queue = [(credit, n, p.relative_to(root) if p.is_relative_to(root) else p, status)
             for status in (PENDING, PROTECTED) for credit, n, p, status in by_frequency(pending, status)]
    answered = review(queue, known, decided, decisions_path, input, log)
    _, left = plan(tracks, known | decided_names(decided), decided)
    waiting = sum(1 for c in left.values() if c[3] == PENDING)

    log("\n--- Review summary ---")
    log(f"{'answered':>20}: {answered}")
    log(f"{'settled by answers':>20}: {max(len(pending) - len(left) - answered, 0)}")
    log(f"{'awaiting decision':>20}: {waiting}")
    log(f"{'protected, unasked':>20}: {len(left) - waiting}")
    log(f"{'decisions':>20}: {decisions_path}")
    log(f"{'to write the tags':>20}: music-librarian split --apply (or the weekly enrich)")
    return 0


def split_pass(opts: Options, log):
    """A split pass, without taking the lock: the caller holds it when `opts.apply`."""
    started = time.time()
    root, state_dir, decisions_path, scan = opts.root, opts.state_dir, opts.decisions, opts.scan
    log(f"[{'APPLY' if opts.apply else 'DRY-RUN'}] splitting credits: "
        f"{', '.join(map(str, scan))}")
    decided = decisions.load(decisions_path, log)

    stats = defaultdict(int)
    tracks = read_tracks(scan, state_dir, log, stats)
    known = library_names(root, state_dir)
    changes, pending = plan(tracks, known | decided_names(decided), decided)

    for (field, old, new), files in sorted(changes.items()):
        log(f"SPLIT    {field}: {old}\n         → {new}  ({count(len(files), 'track')})")
    for credit, n, _, _ in by_frequency(pending, PENDING):
        log(f"REVIEW   {credit}  ({count(n, 'track')})")
    for credit, n, _, _ in by_frequency(pending, PROTECTED):
        log(f"GUARDED  {credit}  ({count(n, 'track')}): an album artist, kept unless decided otherwise")

    writes = [(p, field, old, new) for (field, old, new), files in sorted(changes.items())
              for p in files]
    journal = Journal(state_dir, dict(root=str(root), started=started, argv=sys.argv[1:]))
    stopped = False
    if opts.apply:
        stopped = rewrite_tags(writes, journal, log, stats)
        new_pending = [c for k, (c, _, _, status) in sorted(pending.items())
                       if status == PENDING and k not in decided]
        if new_pending:
            decisions.append_pending(decisions_path, new_pending)
    journal.close()

    stats["credits to split"] = len(changes)
    stats["tags to rewrite"] = len(writes)
    stats["awaiting decision"] = len(by_frequency(pending, PENDING))
    stats["protected"] = len(by_frequency(pending, PROTECTED))
    log("\n--- Split summary ---")
    for k in ("scanned", "credits to split", "tags to rewrite", "rewritten",
              "awaiting decision", "protected", "skipped", "errors"):
        if stats[k]:
            log(f"{k:>20}: {stats[k]}")
    if pending:
        log(f"{'decisions':>20}: {decisions_path}")
        log(f"{'review':>20}: music-librarian split --review")
    return finish(journal, root, log, stats, stopped)


if __name__ == "__main__":
    handle_stop_signals()
    sys.exit(main())
