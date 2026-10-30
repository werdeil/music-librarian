"""Reading and rewriting the credits of the library, shared by split and dedupe."""
import contextlib
import dataclasses
import shlex
import sys
from pathlib import Path

from music_librarian.core import arguments
from music_librarian.core.files import AUDIO_EXT, is_junk
from music_librarian.core.lock import EXIT_LOCKED, EXIT_STOPPED, STOP, Locked, locked
from music_librarian.core.log import count
from music_librarian.core.state import state_path
from music_librarian.media import credit_tags
from music_librarian.media.metadata_cache import MetadataCache
from music_librarian.organize.scan import collect


@dataclasses.dataclass
class CreditOptions:
    """A pass over the credits. `paths` defaults to the whole root."""
    root: Path
    paths: list = dataclasses.field(default_factory=list)
    apply: bool = False
    state_dir: Path = dataclasses.field(default_factory=state_path)

    def __post_init__(self):
        self.root = Path(self.root).resolve()
        self.paths = [Path(p).resolve() for p in self.paths]

    @property
    def scan(self):
        return self.paths or [self.root]


def read_tracks(scan, state_dir, log, stats):
    """[(path, credits)] of the audio files under scan, through the metadata cache."""
    tracks = []
    with MetadataCache(state_dir) as cache:
        for p in collect(scan):
            if is_junk(p) or p.suffix.lower() not in AUDIO_EXT:
                continue
            credits_of = credit_tags.from_snapshot(cache.snapshot(p))
            if credits_of is None:
                log(f"SKIPPED  (unreadable)  {p}")
                stats["skipped"] += 1
            else:
                tracks.append((p, credits_of))
    stats["scanned"] = len(tracks)
    return tracks


def rewrite_tags(writes, journal, log, stats):
    """Write each (path, field, old, new) and journal it; True if a stop request cut it short."""
    for i, (p, field, old, new) in enumerate(writes):
        if STOP.is_set():
            log(f"STOP     requested: {count(len(writes) - i, 'tag')} not rewritten, left for the next pass")
            return True
        try:
            credit_tags.write(p, field, new)
        except Exception as e:
            log(f"ERROR    ({e})  {p}")
            stats["errors"] += 1
            continue
        journal.tag(p, field, old, new)
        stats["rewritten"] += 1
    return False


def parser(command, description, review_help):
    """Arguments shared by the credit commands: --root, paths, --apply or --review."""
    ap = arguments.parser(command, description)
    arguments.add_root(ap)
    ap.add_argument("paths", nargs="*", help="folders to process (default: --root)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="rewrite the tags (otherwise dry run)")
    mode.add_argument("--review", action="store_true", help=review_help)
    arguments.add_state_dir(ap)
    arguments.add_syslog(ap)
    return ap


def run_locked(tag_pass, opts, log):
    """tag_pass(opts, log), under the lock with `opts.apply`."""
    try:
        with locked(opts.state_dir) if opts.apply else contextlib.nullcontext():
            return tag_pass(opts, log)
    except Locked as e:
        log(f"LOCKED   another pass is running ({e}), giving up")
        return EXIT_LOCKED


def run_review(review_pass, opts, log):
    """review_pass(opts, log), in a terminal only; it takes no lock."""
    if not sys.stdin.isatty():
        log("ERROR    --review asks questions: run it in a terminal (docker compose run, not up)")
        return 2
    return review_pass(opts, log)


def finish(journal, root, log, stats, stopped):
    """Log the journal and how to undo it; return the exit code of a tag pass."""
    if journal.path:
        log(f"{'journal':>20}: {journal.path}")
        log(f"{'undo':>20}: music-librarian undo {shlex.quote(str(journal.path))} "
            f"--root {shlex.quote(str(root))} --apply")
    return EXIT_STOPPED if stopped else 2 if stats["skipped"] or stats["errors"] else 0
