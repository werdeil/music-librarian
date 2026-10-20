"""
importer — imports what is dropped in the Inbox into the library.

For each Inbox item (an album folder, or a loose file):
  1. FLAC → ALAC conversion (ffmpeg for the audio, mutagen for the tags and
     artwork), duration (ffprobe) and tag checks;
  2. filing with organize, all or nothing: on any problem the whole item
     goes to _failed/;
  3. enrichment (ReplayGain, cover, lyrics) of the target folders only;
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
import io
import json
import os
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from mutagen.flac import FLAC
from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm

from music_librarian import organize
from music_librarian.core.files import AUDIO_EXT, is_junk
from music_librarian.core.lock import EXIT_LOCKED, EXIT_STOPPED, STOP, Locked, handle_stop_signals, locked
from music_librarian.core.log import Log
from music_librarian.core.state import state_path
from music_librarian.organize.scan import parse_pair, read_tags

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
MAX_DRIFT = 0.1  # duration drift allowed between FLAC and ALAC, in seconds
OK, FAILED, STOPPED = "ok", "failed", "stopped"  # outcome of an item


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


# ---------- ffprobe / conversion ----------

def probe(p: Path):
    """(audio codec, duration in seconds)"""
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "format=duration:stream=codec_type,codec_name", "-of", "json", str(p)],
                         capture_output=True, text=True, check=True)
    j = json.loads(out.stdout)
    codec = next((s.get("codec_name") for s in j.get("streams", [])
                  if s.get("codec_type") == "audio"), None)
    return codec, float(j.get("format", {}).get("duration") or 0)


# Vorbis (FLAC) → MP4. The rest is kept as iTunes freeform fields, except
# REPLAYGAIN_*: enrich.replaygain computes the gain as for the rest of the library.
MP4_TEXT = {
    "title": "\xa9nam", "artist": "\xa9ART", "albumartist": "aART", "album artist": "aART",
    "album": "\xa9alb", "date": "\xa9day", "genre": "\xa9gen", "composer": "\xa9wrt",
    "comment": "\xa9cmt", "description": "\xa9cmt", "lyrics": "\xa9lyr",
    "unsyncedlyrics": "\xa9lyr", "grouping": "\xa9grp", "copyright": "cprt",
    "titlesort": "sonm", "artistsort": "soar", "albumsort": "soal",
    "albumartistsort": "soaa", "composersort": "soco",
}
NUMBERS = {"tracknumber", "tracktotal", "totaltracks", "discnumber", "disctotal",
           "totaldiscs", "compilation", "bpm"}


def copy_tags(flac: FLAC, mp4: MP4):
    t = flac.tags or {}
    keys = {k.lower(): t[k] for k in t.keys()}
    for k, v in keys.items():
        if k in MP4_TEXT:
            mp4.tags[MP4_TEXT[k]] = list(mp4.tags.get(MP4_TEXT[k], [])) + list(v)
        elif k not in NUMBERS and not k.startswith("replaygain_"):
            mp4.tags[f"----:com.apple.iTunes:{k.upper()}"] = [
                MP4FreeForm(x.encode("utf-8")) for x in v]

    num = lambda *ks: next((parse_pair(keys[k][0]) for k in ks if k in keys),
                           (None, None))
    trk, trk_total = num("tracknumber")
    disc, disc_total = num("discnumber")
    trk_total = trk_total or num("tracktotal", "totaltracks")[0]
    disc_total = disc_total or num("disctotal", "totaldiscs")[0]
    if trk:
        mp4.tags["trkn"] = [(trk, trk_total or 0)]
    if disc:
        mp4.tags["disk"] = [(disc, disc_total or 0)]
    if keys.get("compilation", ["0"])[0].strip() == "1":
        mp4.tags["cpil"] = True
    bpm = num("bpm")[0]
    if bpm:
        mp4.tags["tmpo"] = [bpm]

    pics = sorted(flac.pictures, key=lambda p: p.type != 3)   # front cover first
    if pics:
        fmt = MP4Cover.FORMAT_PNG if pics[0].mime == "image/png" else MP4Cover.FORMAT_JPEG
        mp4.tags["covr"] = [MP4Cover(pics[0].data, imageformat=fmt)]


CHECKED = ("albumartist", "artist", "album", "title", "track", "disc", "disctotal",
           "compilation")


def convert(src: Path, dst: Path):
    """FLAC → ALAC, then checks. Raises Reject if anything is off."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(src),
                        "-map", "0:a:0", "-c:a", "alac", "-map_metadata", "-1",
                        "-fflags", "+bitexact", str(dst)],
                       capture_output=True, text=True, check=False)
    if r.returncode:
        raise Reject(f"ffmpeg failed on {src.name}: {r.stderr.strip()[-300:]}")

    flac = FLAC(src)
    mp4 = MP4(dst)
    if mp4.tags is None:
        mp4.add_tags()
    copy_tags(flac, mp4)
    mp4.save()

    _, d_src = probe(src)
    codec, d_dst = probe(dst)
    if codec != "alac" or abs(d_src - d_dst) > MAX_DRIFT:
        raise Reject(f"check failed on {src.name}: {codec}, "
                     f"duration {d_src:.2f}s → {d_dst:.2f}s")
    a, b = read_tags(src) or {}, read_tags(dst) or {}
    diff = [k for k in CHECKED if a.get(k) != b.get(k)]
    if diff:
        raise Reject(f"tags differ after converting {src.name}: {', '.join(diff)}")
    if flac.pictures and "covr" not in MP4(dst).tags:
        raise Reject(f"cover lost converting {src.name}")


# ---------- enrichment ----------

def enrichers():
    """(name, command) called with the target folders as arguments."""
    return [
        ("replaygain", [sys.executable, "-m", "music_librarian.enrich.replaygain"]),
        ("covers", [sys.executable, "-m", "music_librarian.enrich.covers"]),
        ("lyrics", [sys.executable, "-m", "music_librarian.enrich.lyrics"]),
    ]


def enrich(dirs, log):
    ok = True
    for name, cmd in enrichers():
        log(f"ENRICH   ({name})  {len(dirs)} folder(s)")
        try:
            r = subprocess.run([*cmd, *map(str, dirs)], check=False)
            failed = r.returncode != 0
        except OSError as e:
            failed = True
            log(f"         {e}")
        if failed:
            log(f"FAILED   enrichment {name}: caught up by the weekly pass")
            ok = False
    return ok


# ---------- item processing ----------

def organize_paths(args, paths, apply, quiet=False):
    argv = ["--root", str(args.root), *map(str, paths)]
    if apply:
        argv.append("--apply")
    if args.state_dir:
        argv += ["--state-dir", args.state_dir]
    if args.syslog and not quiet:
        argv.append("--syslog")
    # interruptible=False: an item is filed entirely or not at all
    if not quiet:
        return organize.run(argv, interruptible=False)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code, report = organize.run(argv, interruptible=False)
    report["output"] = buf.getvalue()
    return code, report


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


def to_errors(args, item, reason, log):
    log(f"ERROR    {item.name}: {reason}")
    if args.apply:
        dst = free_name(args.inbox / ERRORS, item.name, item.is_dir)
        shutil.move(str(item.path), str(dst))
        log(f"         → {dst}")


def process(args, item, log):
    """Return (OK | FAILED | STOPPED, target folders)."""
    try:
        flacs, audio, annexes, left, junk = classify(item)
    except (Reject, subprocess.CalledProcessError, ValueError) as e:
        to_errors(args, item, e, log)
        return FAILED, set()

    log(f"\n=== {item.name}  ({len(flacs)} FLAC, {len(audio)} ALAC/MP3, "
        f"{len(annexes)} side file(s))")
    names = annex_names(item, annexes)
    for p, rel in names.items():
        if rel.name != p.name:
            log(f"COVER    {item.rel(p)} → {rel.name}")
    for p in left:
        log(f"KEPT     {item.rel(p)}: kept with the original in {DONE}/")

    if not args.apply:
        for p in flacs:
            log(f"CONVERT  {item.rel(p)}  → .m4a")
        code, report = organize_paths(args, [item.path], apply=False, quiet=True)
        if code:
            log(report["output"].rstrip())
            log(f"ERROR    {item.name}: would go to {ERRORS}/ (see above)")
            return FAILED, set()
        for src, dst in report["moves"]:
            if src in left:
                continue
            if src.suffix.lower() == ".flac":
                dst = dst.with_suffix(".m4a")
            elif src in names:
                dst = dst.with_name(names[src].name)
            log(f"ORGANIZE {item.rel(src)}\n         → {dst}")
        return OK, set()

    work = args.inbox / WORK / item.name
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
        code, report = organize_paths(args, [work], apply=False, quiet=True)
        if code:
            log(report["output"].rstrip())
            raise Reject("refused by organize (see above)")
    except (Reject, Stopped, OSError, subprocess.CalledProcessError, ValueError) as e:
        shutil.rmtree(work, ignore_errors=True)
        with contextlib.suppress(OSError):
            work.parent.rmdir()
        if isinstance(e, Stopped):
            log(f"STOP     {item.name}: left in the Inbox for the next pass")
            return STOPPED, set()
        to_errors(args, item, e, log)
        return FAILED, set()

    # 2. Actual filing.
    code, report = organize_paths(args, [work], apply=True)
    if code:
        log(f"ERROR    {item.name}: filing incomplete, item and {work} left "
            f"in place (journal: {report.get('journal')})")
        return FAILED, set()
    targets = {dst.parent for src, dst in report["done"] if dst.suffix.lower() in AUDIO_EXT}

    # 3. The original goes to _done/; what is left in the work folder (side
    #    files that did not follow) joins it.
    day = args.inbox / DONE / date.today().isoformat()
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


def purge(args, log):
    limit = date.today() - timedelta(days=args.keep_days)
    d = args.inbox / DONE
    if not d.is_dir():
        return
    for p in sorted(d.iterdir()):
        try:
            day = date.fromisoformat(p.name)
        except ValueError:
            continue
        if p.is_dir() and day < limit:
            log(f"PURGE    {p}")
            if args.apply:
                shutil.rmtree(p)


# ---------- main ----------

def main(argv=None):
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
    ap.add_argument("--no-enrich", action="store_true", help="without ReplayGain/cover/lyrics")
    ap.add_argument("--state-dir", default=os.environ.get("MUSIC_LIBRARIAN_STATE"))
    ap.add_argument("--syslog", action="store_true",
                    help="also send the lines to syslog/journald (tag music-import)")
    args = ap.parse_args(argv)
    args.inbox, args.root = args.inbox.resolve(), args.root.resolve()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    log = Log(args.syslog, ident="music-import")
    log(f"[{'APPLY' if args.apply else 'DRY-RUN'}] inbox: {args.inbox}")
    state_dir = state_path(args.state_dir)
    try:
        with locked(state_dir) if args.apply else contextlib.nullcontext():
            return import_all(args, log)
    except Locked as e:
        log(f"LOCKED   another pass is running ({e}), giving up")
        return EXIT_LOCKED


def import_all(args, log):
    if not args.root.is_dir():
        log(f"ERROR    library not found: {args.root}")
        return 2
    if args.apply:
        for d in ERRORS, DONE:
            (args.inbox / d).mkdir(parents=True, exist_ok=True)
    elif not args.inbox.is_dir():
        log("Inbox missing: it will be created with --apply")
        return 0

    purge(args, log)

    if args.apply:
        for p in args.inbox.iterdir():
            if p.is_file() and is_junk(p):
                p.unlink(missing_ok=True)
    items = list_items(args.inbox)
    if not items:
        log("Inbox empty")
        return 0

    items, busy = stable_items(items, args.settle)
    for it in busy:
        log(f"WAITING  {it.name}: copy in progress, handled on the next pass")

    counts, targets = defaultdict(int), set()
    for i, it in enumerate(items):
        if STOP.is_set():
            counts["left (stopped)"] += len(items) - i
            log(f"STOP     requested: {len(items) - i} item(s) left for the next pass")
            break
        status, t = process(args, it, log)
        counts[status] += 1
        targets |= t

    enrich_ok = True
    if targets and not args.no_enrich:
        log("")
        if STOP.is_set():
            log("STOP     enrichment skipped: caught up by the weekly pass")
        else:
            enrich_ok = enrich(sorted(targets), log)

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
