"""
dedupe — finds the names of one artist spelled several ways, and unifies them in the tags.

Names are read from the album artist, artist and composer tags, cut on ";" as Lyrion
does. Names are grouped as likely duplicates when they are:
  - the same once case, accents, punctuation, "&"/"and"/"et", a leading "The" and the
    "Last, First" order are set aside (Beyonce / Beyoncé, R.E.M. / REM);
  - credited with the same MusicBrainz artist id;
  - one typo apart (Mettalica / Metallica);
  - a single word ending exactly one longer name (Bach / Johann Sebastian Bach).
Names credited with different MusicBrainz ids are never grouped.

The answers live in state/artist-aliases.txt: "VARIANT => NAME" rewrites a variant,
"NAME != OTHER" keeps two names apart. --review asks about the groups one at a time
(see dedupe/review.py); --apply rewrites the variants in the tags.

  music-librarian dedupe                    # dry run: the groups found, the tags to rewrite
  music-librarian dedupe --review           # answer the groups (writes the aliases file only)
  music-librarian dedupe --apply [PATH...]  # rewrite the variants

A renamed album artist moves its albums on the next organize pass. Every tag change
goes through the journal: `music-librarian undo` restores it.

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
from music_librarian.dedupe import aliases as aliases_file
from music_librarian.dedupe.aliases import canonical
from music_librarian.dedupe.review import pending, review
from music_librarian.dedupe.similar import find_groups
from music_librarian.split.library import (CreditOptions, finish, parser, read_tracks, rewrite_tags,
                                           run_locked, run_review)
from music_librarian.split.rules import LYRION_SEPARATOR, key, names_of

FIELDS = ("albumartist", "artist", "composer")


@dataclasses.dataclass
class Options(CreditOptions):
    """A dedupe pass; `aliases` defaults to the state folder's file."""
    aliases: Path | None = None

    def __post_init__(self):
        super().__post_init__()
        self.aliases = self.aliases or self.state_dir / aliases_file.FILE


def main(argv=None):
    ap = parser("dedupe", "Unify the names of one artist spelled several ways.",
                "answer the groups of likely duplicates, one at a time (writes the aliases file only)")
    ap.add_argument("--aliases", type=Path, help=f"aliases file (default: state/{aliases_file.FILE})")
    args = ap.parse_args(argv)
    opts = Options(root=args.root, paths=args.paths, apply=args.apply,
                   state_dir=state_path(args.state_dir), aliases=args.aliases)
    log = Log(args.syslog)
    if args.review:
        return run_review(review_pass, opts, log)
    return recorded(lambda: run(opts, log), log, opts.state_dir, "dedupe", opts.apply)


def run(opts: Options, log):
    """A dedupe pass, under the lock with `apply`."""
    return run_locked(dedupe_pass, opts, log)


def names_in(tracks, aliases):
    """({name: tags crediting it}, {name: MusicBrainz ids}), each name as the aliases rewrite it.
    An id is only tied to a tag that credits a single name."""
    counts, mbids = defaultdict(int), defaultdict(set)
    for _, tags in tracks:
        for field in FIELDS:
            names = [n for value in tags[field] for n in names_of(value)]
            for n in names:
                counts[canonical(n, aliases)] += 1
            ids = tags.get(f"{field}_mbid") or []
            if len(names) == 1 and len(ids) == 1:
                mbids[canonical(names[0], aliases)].add(ids[0])
    return counts, mbids


def plan(tracks, aliases):
    """{(field, old, new): [files]} for the tags crediting a variant."""
    changes = defaultdict(list)
    for p, tags in tracks:
        for field in FIELDS:
            if len(tags[field]) != 1:
                continue
            old = tags[field][0]
            names = names_of(old)
            renamed = [canonical(n, aliases) for n in names]
            if renamed != names:
                unique = {}
                for n in renamed:
                    unique.setdefault(key(n), n)
                changes[(field, old, LYRION_SEPARATOR.join(unique.values()))].append(p)
    return changes


def open_groups(tracks, path, log):
    aliases, different = aliases_file.load(path, log)
    counts, mbids = names_in(tracks, aliases)
    groups = [(names, reasons) for names, reasons in find_groups(counts, mbids, different)
              if pending(names, aliases, different)]
    return groups, counts


def review_pass(opts: Options, log):
    log(f"reading the credits of {', '.join(map(str, opts.scan))}…")
    tracks = read_tracks(opts.scan, opts.state_dir, log, defaultdict(int))
    path = opts.aliases
    groups, counts = open_groups(tracks, path, log)
    answered = review(groups, counts, path, input, log)
    left, _ = open_groups(tracks, path, log)

    log("\n--- Review summary ---")
    log(f"{'answered':>20}: {answered}")
    log(f"{'groups left':>20}: {len(left)}")
    log(f"{'aliases':>20}: {path}")
    log(f"{'to write the tags':>20}: music-librarian dedupe --apply (or the weekly enrich)")
    return 0


def dedupe_pass(opts: Options, log):
    """A dedupe pass, without taking the lock: the caller holds it when `opts.apply`."""
    started = time.time()
    root, state_dir, scan = opts.root, opts.state_dir, opts.scan
    log(f"[{'APPLY' if opts.apply else 'DRY-RUN'}] unifying names: {', '.join(map(str, scan))}")
    stats = defaultdict(int)
    tracks = read_tracks(scan, state_dir, log, stats)
    path = opts.aliases
    changes = plan(tracks, aliases_file.load(path, log)[0])
    for (field, old, new), files in sorted(changes.items()):
        log(f"RENAME   {field}: {old}\n         → {new}  ({count(len(files), 'track')})")
    groups = []
    if not opts.apply:
        groups, counts = open_groups(tracks, path, log)
        for names, reasons in groups:
            log(f"SIMILAR  {' · '.join(f'{n} ({counts[n]})' for n in names)}  "
                f"[{', '.join(sorted(reasons))}]")

    writes = [(p, field, old, new) for (field, old, new), files in sorted(changes.items())
              for p in files]
    journal = Journal(state_dir, dict(root=str(root), started=started, argv=sys.argv[1:]))
    stopped = rewrite_tags(writes, journal, log, stats) if opts.apply else False
    journal.close()

    stats["tags to rewrite"] = len(writes)
    stats["album artists"] = sum(1 for p, field, _, _ in writes if field == "albumartist")
    stats["groups to review"] = len(groups)
    log("\n--- Dedupe summary ---")
    for k in ("scanned", "tags to rewrite", "rewritten", "groups to review", "skipped", "errors"):
        if stats[k]:
            log(f"{k:>20}: {stats[k]}")
    if stats["album artists"]:
        log(f"{'album artists':>20}: {stats['album artists']} renamed, filed by the next organize pass")
    if groups:
        log(f"{'review':>20}: music-librarian dedupe --review")
    return finish(journal, root, log, stats, stopped)


if __name__ == "__main__":
    handle_stop_signals()
    sys.exit(main())
