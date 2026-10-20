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

A full --apply pass keeps the names it saw in state/split-names.json, so a pass
over a few folders (the one import runs) still knows the whole library.
Every tag change goes through the journal: `music-librarian undo` restores it.

Exit codes: 0 = OK, 2 = unreadable files or failed writes, 3 = another pass is
running, 4 = stopped before the end.
"""
import argparse
import contextlib
import dataclasses
import json
import os
import shlex
import sys
import time
from collections import defaultdict
from pathlib import Path

from music_librarian.core.files import AUDIO_EXT, is_junk
from music_librarian.core.journal import Journal
from music_librarian.core.lock import EXIT_LOCKED, EXIT_STOPPED, STOP, Locked, handle_stop_signals, locked
from music_librarian.core.log import Log
from music_librarian.core.state import state_path
from music_librarian.media import credit_tags
from music_librarian.organize.scan import collect
from music_librarian.split import decisions
from music_librarian.split.review import review
from music_librarian.split.rules import decided_names, key, known_names, rewrite

NAMES = "split-names.json"
FIELDS = ("composer", "artist")


@dataclasses.dataclass
class Options:
    """A split pass. `paths` defaults to the whole root, `decisions` to the state folder's file."""
    root: Path
    paths: list = dataclasses.field(default_factory=list)
    apply: bool = False
    decisions: Path | None = None
    state_dir: Path = dataclasses.field(default_factory=state_path)

    def __post_init__(self):
        self.root = Path(self.root).resolve()
        self.paths = [Path(p).resolve() for p in self.paths]
        self.decisions = self.decisions or self.state_dir / decisions.FILE


def parser():
    ap = argparse.ArgumentParser(description="Split the credits that hold several names.")
    root_env = os.environ.get("MUSIC_LIBRARIAN_ROOT")
    ap.add_argument("--root", default=root_env, required=not root_env,
                    help="library root (default: $MUSIC_LIBRARIAN_ROOT)")
    ap.add_argument("paths", nargs="*", help="folders to process (default: --root)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="write the tags (otherwise dry run)")
    mode.add_argument("--review", action="store_true",
                      help="answer the credits awaiting a decision, one at a time (writes the "
                           "decisions file only)")
    ap.add_argument("--decisions", type=Path,
                    help=f"decisions file (default: state/{decisions.FILE})")
    ap.add_argument("--state-dir", default=os.environ.get("MUSIC_LIBRARIAN_STATE"))
    ap.add_argument("--syslog", action="store_true",
                    help="also send the lines to syslog/journald (tag music-librarian)")
    return ap


def main(argv=None):
    args = parser().parse_args(argv)
    opts = Options(root=args.root, paths=args.paths, apply=args.apply, decisions=args.decisions,
                   state_dir=state_path(args.state_dir))
    log = Log(args.syslog)
    return review_pass(opts, log) if args.review else run(opts, log)


def run(opts: Options, log):
    """A split pass, under the lock with `apply`."""
    try:
        with locked(opts.state_dir) if opts.apply else contextlib.nullcontext():
            return split_pass(opts, log)
    except Locked as e:
        log(f"LOCKED   another pass is running ({e}), giving up")
        return EXIT_LOCKED


def read_tracks(scan, log, stats):
    tracks = []
    for p in collect(scan):
        if is_junk(p) or p.suffix.lower() not in AUDIO_EXT:
            continue
        try:
            tags = credit_tags.read(p)
        except Exception as e:
            log(f"SKIPPED  (unreadable: {e})  {p}")
            stats["skipped"] += 1
            continue
        if tags:
            tracks.append((p, tags))
    stats["scanned"] = len(tracks)
    return tracks


def library_names(tracks, cache: Path, full):
    names = known_names([v for _, t in tracks for v in t["albumartist"]],
                        [v for _, t in tracks for f in FIELDS for v in t[f]])
    if not full:
        with contextlib.suppress(OSError, ValueError):
            names |= set(json.loads(cache.read_text(encoding="utf-8")))
    return names


def plan(tracks, known, decided):
    """({(field, old, new): [files]}, {key: [credit, tracks, first file]} awaiting a decision)."""
    changes, pending = defaultdict(list), {}
    for p, tags in tracks:
        for field in FIELDS:
            values = tags[field]
            if len(values) != 1 or (field == "artist" and not tags["albumartist"]):
                continue
            new, waiting = rewrite(values[0], known, decided)
            for credit in waiting:
                pending.setdefault(key(credit), [credit, 0, p])[1] += 1
            if new:
                changes[(field, values[0], new)].append(p)
    return changes, pending


def by_frequency(pending):
    return sorted(pending.values(), key=lambda c: (-c[1], key(c[0])))


def review_pass(opts: Options, log):
    """Asks about the credits awaiting a decision; writes the decisions file only, without the lock."""
    if not sys.stdin.isatty():
        log("ERROR    --review asks questions: run it in a terminal (docker compose run, not up)")
        return 2
    root, decisions_path = opts.root, opts.decisions
    scan = opts.paths or [root]
    decided = decisions.load(decisions_path, log)
    stats = defaultdict(int)
    log(f"reading the credits of {', '.join(map(str, scan))}…")
    tracks = read_tracks(scan, log, stats)
    known = library_names(tracks, opts.state_dir / NAMES, scan == [root])
    _, pending = plan(tracks, known | decided_names(decided), decided)
    queue = [(credit, n, p.relative_to(root) if p.is_relative_to(root) else p)
             for credit, n, p in by_frequency(pending)]
    answered = review(queue, known, decided, decisions_path, input, log)
    _, left = plan(tracks, known | decided_names(decided), decided)

    log("\n--- Review summary ---")
    log(f"{'answered':>20}: {answered}")
    log(f"{'settled by answers':>20}: {max(len(pending) - len(left) - answered, 0)}")
    log(f"{'awaiting decision':>20}: {len(left)}")
    log(f"{'decisions':>20}: {decisions_path}")
    log(f"{'to write the tags':>20}: music-librarian split --apply (or the weekly enrich)")
    return 0


def split_pass(opts: Options, log):
    """A split pass, without taking the lock: the caller holds it when `opts.apply`."""
    started = time.time()
    root, state_dir, decisions_path = opts.root, opts.state_dir, opts.decisions
    scan = opts.paths or [root]
    full = scan == [root]
    log(f"[{'APPLY' if opts.apply else 'DRY-RUN'}] splitting credits: "
        f"{', '.join(map(str, scan))}")
    decided = decisions.load(decisions_path, log)

    stats = defaultdict(int)
    tracks = read_tracks(scan, log, stats)
    known = library_names(tracks, state_dir / NAMES, full)
    changes, pending = plan(tracks, known | decided_names(decided), decided)

    for (field, old, new), files in sorted(changes.items()):
        log(f"SPLIT    {field}: {old}\n         → {new}  ({len(files)} track(s))")
    for credit, n, _ in by_frequency(pending):
        log(f"REVIEW   {credit}  ({n} track(s))")

    writes = [(p, field, old, new) for (field, old, new), files in sorted(changes.items())
              for p in files]
    journal = Journal(state_dir, dict(root=str(root), started=started, argv=sys.argv[1:]))
    stopped = False
    if opts.apply:
        for i, (p, field, old, new) in enumerate(writes):
            if STOP.is_set():
                log(f"STOP     requested: {len(writes) - i} tag(s) not rewritten, "
                    f"left for the next pass")
                stopped = True
                break
            try:
                credit_tags.write(p, field, new)
            except Exception as e:
                log(f"ERROR    ({e})  {p}")
                stats["errors"] += 1
                continue
            journal.tag(p, field, old, new)
            stats["rewritten"] += 1
        new_pending = [c for k, (c, _, _) in sorted(pending.items()) if k not in decided]
        if new_pending:
            decisions.append_pending(decisions_path, new_pending)
        if full:
            state_dir.mkdir(parents=True, exist_ok=True)
            (state_dir / NAMES).write_text(json.dumps(sorted(known), ensure_ascii=False),
                                           encoding="utf-8")
    journal.close()

    stats["credits to split"] = len(changes)
    stats["tags to rewrite"] = len(writes)
    stats["awaiting decision"] = len(pending)
    log("\n--- Split summary ---")
    for k in ("scanned", "credits to split", "tags to rewrite", "rewritten",
              "awaiting decision", "skipped", "errors"):
        if stats[k]:
            log(f"{k:>20}: {stats[k]}")
    if pending:
        log(f"{'decisions':>20}: {decisions_path}")
        log(f"{'review':>20}: music-librarian split --review")
    if journal.path:
        log(f"{'journal':>20}: {journal.path}")
        log(f"{'undo':>20}: music-librarian undo {shlex.quote(str(journal.path))} "
            f"--root {shlex.quote(str(root))} --apply")
    return EXIT_STOPPED if stopped else 2 if stats["skipped"] or stats["errors"] else 0


if __name__ == "__main__":
    handle_stop_signals()
    sys.exit(main())
