"""
importer — imports what is dropped in the Inbox into the library.

For each Inbox item (an album folder, or a loose file):
  1. FLAC → ALAC conversion and its checks (media/convert.py);
  2. filing with organize, all or nothing: on any problem the whole item
     goes to _failed/;
  3. enrichment (genres, ReplayGain, cover, lyrics) of the target folders only,
     then the split of their credits holding several names (split/) and the
     spellings decided in the artist aliases (dedupe/);
  4. the original item (FLAC included) goes to _done/YYYY-MM-DD/, purged
     after 7 days.

Dry run by default. --apply to actually do it.

  music-librarian import            # see what would be done
  music-librarian import --apply    # import

Paths: --inbox / $MUSIC_LIBRARIAN_INBOX, --root / $MUSIC_LIBRARIAN_ROOT.
One --apply pass at a time (lock shared with organize). On SIGTERM or
Ctrl-C, the item being filed is completed; the one being converted and the
following ones stay in the Inbox for the next pass.

Exit codes: 0 = OK, 2 = at least one item failed or an enrichment failed,
3 = another pass is running, 4 = stopped before the end.
"""
import argparse
import contextlib
import os
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from music_librarian import dedupe, enrich, organize, split
from music_librarian.core.files import AUDIO_EXT, is_junk
from music_librarian.core.lock import EXIT_LOCKED, EXIT_STOPPED, STOP, Locked, handle_stop_signals, locked
from music_librarian.core.log import Buffer, Log
from music_librarian.core.state import state_path
from music_librarian.integrations import lyrion
from music_librarian.integrations.lyrion import Lyrion
from music_librarian.media.convert import ConversionError, convert, probe

ERRORS, DONE, WORK = "_failed", "_done", "_processing"
UNFILED = "_unfiled"  # side files of a done item that did not follow it
RESERVED = {ERRORS, DONE, WORK}
# audio formats recognized but not handled: the item goes to _failed
OTHER_AUDIO = {".wav", ".aif", ".aiff", ".ogg", ".oga", ".opus", ".wma", ".ape", ".wv",
               ".dsf", ".dff", ".aac", ".m4b", ".mp2", ".mka", ".alac", ".mpc", ".tta"}
# side files that follow the album into the library; the others (.txt, .nfo,
# .log…) stay with the original in _done/
ANNEX_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".pdf", ".lrc", ".cue"}
# covers renamed to folder.jpg (the library's name, and the one enrich/covers.py
# looks for), unless the folder already has one
COVER = "folder.jpg"
COVER_NAMES = {"cover.jpg", "cover.jpeg", "front.jpg", "front.jpeg"}
OK, FAILED, STOPPED = "ok", "failed", "stopped"  # outcome of an item


@dataclass
class Options:  # pylint: disable=too-many-instance-attributes  # one per command-line option
    inbox: Path
    root: Path
    apply: bool = False
    settle: float = 10
    keep_days: int = 7
    enrich: bool = True
    split: bool = True
    dedupe: bool = True
    state_dir: Path = field(default_factory=state_path)
    syslog: bool = False
    lyrion: Lyrion | None = None

    def __post_init__(self):
        self.inbox, self.root = Path(self.inbox).resolve(), Path(self.root).resolve()


class Reject(Exception):
    """Problem with an item: it goes to _failed."""


class Stopped(Exception):
    """Stop requested while an item is being prepared: it stays in the Inbox."""


# ---------- Inbox items ----------

class Item:
    def __init__(self, path: Path):
        self.path = path                      # album folder or loose file
        self.name = path.name
        self.is_dir = path.is_dir()

    def files(self):
        if not self.is_dir:
            return [self.path]
        out = []
        for dirpath, dirnames, filenames in os.walk(self.path):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            out += [Path(dirpath) / n for n in sorted(filenames)]
        return out

    def rel(self, p: Path):
        return p.relative_to(self.path) if self.is_dir else Path(p.name)

    def snapshot(self):
        snap = []
        for p in self.files():
            try:
                st = p.stat()
                snap.append((str(p), st.st_size, st.st_mtime))
            except OSError:
                snap.append((str(p), None, None))
        return snap


def list_items(inbox: Path):
    return [Item(p) for p in sorted(inbox.iterdir())
            if p.name not in RESERVED and not p.name.startswith(".")]


def stable_items(items, settle):
    """Keep the items where no file changed for `settle` seconds (copy
    finished)."""
    if settle <= 0:
        return items, []
    before = {it.name: it.snapshot() for it in items}
    time.sleep(settle)
    ok, busy = [], []
    for it in items:
        (ok if it.snapshot() == before[it.name] else busy).append(it)
    return ok, busy


def free_name(d: Path, name, is_dir=True):
    """d/name, or d/name (2), (3)… if already taken."""
    stem, ext = (name, "") if is_dir else os.path.splitext(name)
    p, i = d / name, 2
    while p.exists():
        p = d / f"{stem} ({i}){ext}"
        i += 1
    return p


# ---------- enrichment ----------

def enrich_targets(dirs, log):
    ok = True
    for name, cmd in enrich.enrichers():
        log(f"ENRICH   ({name})  {len(dirs)} folder(s)")
        try:
            r = subprocess.run([*cmd, *map(str, dirs)], check=False)
            failed = r.returncode not in (0, EXIT_STOPPED)
        except OSError as e:
            failed = True
            log(f"         {e}")
        if not failed and r.returncode == EXIT_STOPPED:
            log(f"STOP     enrichment {name} stopped: caught up by the weekly pass")
        if failed:
            log(f"FAILED   enrichment {name}: caught up by the weekly pass")
            ok = False
    return ok


def fix_credits(opts, dirs, log):
    """Splits, then unifies, the credits of `dirs`, under the lock import already holds."""
    ok = True
    for name, wanted, tag_pass, pass_options in (
            ("credit split", opts.split, split.split_pass, split.Options),
            ("artist aliases", opts.dedupe, dedupe.dedupe_pass, dedupe.Options)):
        if wanted and not STOP.is_set() and tag_pass(
                pass_options(root=opts.root, paths=dirs, apply=True, state_dir=opts.state_dir), log):
            log(f"FAILED   {name}: caught up by the weekly pass")
            ok = False
    return ok


# ---------- item processing ----------

def organize_paths(opts, paths, apply, log):
    """Files `paths` with organize, under the lock import already holds."""
    return organize.organize_pass(organize.Options(
        root=opts.root, paths=paths, apply=apply, state_dir=opts.state_dir,
        lyrion=opts.lyrion, interruptible=False), log)


def classify(item):
    """(flac, other accepted audio, side files, left behind, junk). Raises
    Reject for an unhandled format."""
    flacs, audio, annexes, left, junk = [], [], [], [], []
    for p in item.files():
        ext = p.suffix.lower()
        if is_junk(p):
            junk.append(p)
        elif ext == ".flac":
            flacs.append(p)
        elif ext in AUDIO_EXT:
            if ext == ".m4a":
                codec, _ = probe(p)
                if codec != "alac":
                    raise Reject(f"{p.name}: .m4a in {codec}, not ALAC")
            audio.append(p)
        elif ext in OTHER_AUDIO:
            raise Reject(f"{p.name}: format {ext} not handled")
        elif ext in ANNEX_EXT:
            annexes.append(p)
        else:
            left.append(p)
    if not flacs and not audio:
        raise Reject("no audio file")
    return flacs, audio, annexes, left, junk


def annex_names(item, annexes):
    """Relative name of each side file in the library: cover.jpg → folder.jpg."""
    has_cover = {item.rel(p).parent for p in annexes if p.name.lower() == COVER}
    out = {}
    for p in annexes:
        rel = item.rel(p)
        if p.name.lower() in COVER_NAMES and rel.parent not in has_cover:
            has_cover.add(rel.parent)
            rel = rel.with_name(COVER)
        out[p] = rel
    return out


def link_or_copy(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)        # same disk: instant, the original stays intact
    except OSError:
        shutil.copy2(src, dst)


def to_errors(opts, item, reason, log):
    log(f"ERROR    {item.name}: {reason}")
    if opts.apply:
        dst = free_name(opts.inbox / ERRORS, item.name, item.is_dir)
        shutil.move(str(item.path), str(dst))
        log(f"         → {dst}")


def process(opts, item, log):
    """Return (OK | FAILED | STOPPED, target folders)."""
    try:
        flacs, audio, annexes, left, junk = classify(item)
    except (Reject, subprocess.CalledProcessError, ValueError) as e:
        to_errors(opts, item, e, log)
        return FAILED, set()

    log(f"\n=== {item.name}  ({len(flacs)} FLAC, {len(audio)} ALAC/MP3, "
        f"{len(annexes)} side file(s))")
    names = annex_names(item, annexes)
    for p, rel in names.items():
        if rel.name != p.name:
            log(f"COVER    {item.rel(p)} → {rel.name}")
    for p in left:
        log(f"KEPT     {item.rel(p)}: kept with the original in {DONE}/")

    if not opts.apply:
        for p in flacs:
            log(f"CONVERT  {item.rel(p)}  → .m4a")
        out = Buffer()
        report = organize_paths(opts, [item.path], False, out)
        if report.code:
            log(out.text().rstrip())
            log(f"ERROR    {item.name}: would go to {ERRORS}/ (see above)")
            return FAILED, set()
        for src, dst in report.moves:
            if src in left:
                continue
            if src.suffix.lower() == ".flac":
                dst = dst.with_suffix(".m4a")
            elif src in names:
                dst = dst.with_name(names[src].name)
            log(f"ORGANIZE {item.rel(src)}\n         → {dst}")
        return OK, set()

    work = opts.inbox / WORK / item.name
    if work.exists():
        log(f"ERROR    {item.name}: {work} already exists (interrupted pass?), "
            f"item left in place")
        return FAILED, set()

    # 1. Work folder: converted FLAC, hard-linked ALAC/MP3 and side files.
    try:
        for p in flacs:
            if STOP.is_set():
                raise Stopped
            log(f"CONVERT  {item.rel(p)}")
            convert(p, work / item.rel(p).with_suffix(".m4a"))
        for p in audio:
            link_or_copy(p, work / item.rel(p))
        for p, rel in names.items():
            link_or_copy(p, work / rel)
        out = Buffer()
        if organize_paths(opts, [work], False, out).code:
            log(out.text().rstrip())
            raise Reject("refused by organize (see above)")
    except (Reject, ConversionError, Stopped, OSError, subprocess.CalledProcessError, ValueError) as e:
        shutil.rmtree(work, ignore_errors=True)
        with contextlib.suppress(OSError):
            work.parent.rmdir()
        if isinstance(e, Stopped):
            log(f"STOP     {item.name}: left in the Inbox for the next pass")
            return STOPPED, set()
        to_errors(opts, item, e, log)
        return FAILED, set()

    # 2. Actual filing.
    # organize logs under its own syslog tag
    report = organize_paths(opts, [work], True, Log(opts.syslog))
    if report.code:
        log(f"ERROR    {item.name}: filing incomplete, item and {work} left "
            f"in place (journal: {report.journal})")
        return FAILED, set()
    targets = {dst.parent for src, dst in report.done if dst.suffix.lower() in AUDIO_EXT}

    # 3. The original goes to _done/; what is left in the work folder (side
    #    files that did not follow) joins it.
    day = opts.inbox / DONE / date.today().isoformat()
    day.mkdir(parents=True, exist_ok=True)
    kept = free_name(day, item.name, item.is_dir)
    for j in junk:
        j.unlink(missing_ok=True)
    shutil.move(str(item.path), str(kept))
    leftovers = [p for p in work.rglob("*") if p.is_file()]
    if leftovers:
        rest = kept / UNFILED if kept.is_dir() else free_name(day, f"{item.name}{UNFILED}")
        for p in leftovers:
            link_or_copy(p, rest / p.relative_to(work))
        log(f"NOTE     {len(leftovers)} unfiled file(s) kept in {rest}")
    shutil.rmtree(work)
    with contextlib.suppress(OSError):
        work.parent.rmdir()
    log(f"DONE     {item.name} → {kept}")
    return OK, targets


def purge(opts, log):
    limit = date.today() - timedelta(days=opts.keep_days)
    d = opts.inbox / DONE
    if not d.is_dir():
        return
    for p in sorted(d.iterdir()):
        try:
            day = date.fromisoformat(p.name)
        except ValueError:
            continue
        if p.is_dir() and day < limit:
            log(f"PURGE    {p}")
            if opts.apply:
                shutil.rmtree(p)


# ---------- main ----------

def parser():
    ap = argparse.ArgumentParser(description="Import the Inbox into the library.")
    inbox_env, root_env = os.environ.get("MUSIC_LIBRARIAN_INBOX"), os.environ.get("MUSIC_LIBRARIAN_ROOT")
    ap.add_argument("--inbox", type=Path, default=inbox_env, required=not inbox_env,
                    help="drop folder (default: $MUSIC_LIBRARIAN_INBOX)")
    ap.add_argument("--root", type=Path, default=root_env, required=not root_env,
                    help="library root (default: $MUSIC_LIBRARIAN_ROOT)")
    ap.add_argument("--apply", action="store_true", help="do it (otherwise dry run)")
    ap.add_argument("--settle", type=float, default=10,
                    help="seconds without change before handling an item")
    ap.add_argument("--keep-days", type=int, default=7, help="days _done/ is kept")
    ap.add_argument("--no-enrich", action="store_true", help="without genres/ReplayGain/cover/lyrics")
    ap.add_argument("--no-split", action="store_true", help="without splitting the credits")
    ap.add_argument("--no-dedupe", action="store_true", help="without applying the artist aliases")
    ap.add_argument("--state-dir", default=os.environ.get("MUSIC_LIBRARIAN_STATE"))
    ap.add_argument("--syslog", action="store_true",
                    help="also send the lines to syslog/journald (tag music-import)")
    lyrion.add_arguments(ap)
    return ap


def options(args):
    """Options from the arguments of `parser()`."""
    opts = Options(inbox=args.inbox, root=args.root, apply=args.apply, settle=args.settle,
                   keep_days=args.keep_days, enrich=not args.no_enrich, split=not args.no_split,
                   dedupe=not args.no_dedupe, state_dir=state_path(args.state_dir), syslog=args.syslog)
    opts.lyrion = lyrion.from_args(args, opts.root, opts.state_dir)
    return opts


def new_log(opts):
    return Log(opts.syslog, ident="music-import")


def main(argv=None):
    opts = options(parser().parse_args(argv))
    return run(opts, new_log(opts))


def run(opts: Options, log):
    """An import pass, under the lock with `apply`."""
    log(f"[{'APPLY' if opts.apply else 'DRY-RUN'}] inbox: {opts.inbox}")
    try:
        with locked(opts.state_dir) if opts.apply else contextlib.nullcontext():
            return import_all(opts, log)
    except Locked as e:
        log(f"LOCKED   another pass is running ({e}), giving up")
        return EXIT_LOCKED


def import_all(opts, log):
    if not opts.root.is_dir():
        log(f"ERROR    library not found: {opts.root}")
        return 2
    if opts.apply:
        for d in ERRORS, DONE:
            (opts.inbox / d).mkdir(parents=True, exist_ok=True)
    elif not opts.inbox.is_dir():
        log("Inbox missing: it will be created with --apply")
        return 0

    purge(opts, log)

    if opts.apply:
        for p in opts.inbox.iterdir():
            if p.is_file() and is_junk(p):
                p.unlink(missing_ok=True)
    items = list_items(opts.inbox)
    if not items:
        log("Inbox empty")
        return 0

    items, busy = stable_items(items, opts.settle)
    for it in busy:
        log(f"WAITING  {it.name}: copy in progress, handled on the next pass")

    counts, targets = defaultdict(int), set()
    for i, it in enumerate(items):
        if STOP.is_set():
            counts["left (stopped)"] += len(items) - i
            log(f"STOP     requested: {len(items) - i} item(s) left for the next pass")
            break
        status, t = process(opts, it, log)
        counts[status] += 1
        targets |= t

    enrich_ok = True
    if targets and opts.enrich:
        log("")
        if STOP.is_set():
            log("STOP     enrichment skipped: caught up by the weekly pass")
        else:
            enrich_ok = enrich_targets(sorted(targets), log)
    if targets and not STOP.is_set():
        log("")
        enrich_ok = fix_credits(opts, sorted(targets), log) and enrich_ok

    log("\n--- Import summary ---")
    for k, v in (("imported", counts[OK]), ("failed", counts[FAILED]),
                 ("left (stopped)", counts[STOPPED] + counts["left (stopped)"]),
                 ("waiting", len(busy)), ("target folders", len(targets))):
        if v:
            log(f"{k:>20}: {v}")
    if STOP.is_set():
        return EXIT_STOPPED
    return 2 if counts[FAILED] or not enrich_ok else 0


if __name__ == "__main__":
    handle_stop_signals()
    sys.exit(main())
