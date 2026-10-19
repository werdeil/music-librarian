#!/usr/bin/env python3
"""
music_ranger — files a music library the way Apple Music does:
    <Album Artist>/<Album>/<NN Title>.ext
    Compilations/<Album>/<NN Title>.ext            (compilation flag)
    .../<D-NN Title>.ext                           (multi-disc album)

Dry run by default. --apply to actually do it.

Examples:
  # Check the whole library (nothing is changed)
  music_ranger.py --root /srv/music

  # File an album dropped in the Inbox
  music_ranger.py --root /srv/music /srv/inbox/MyAlbum --apply

  # Only process the folders changed since the last pass
  music_ranger.py --root /srv/music --since --apply

  # Undo a pass (dry run, then --apply)
  music_ranger.py --root /srv/music --undo state/journal/<date>.jsonl

With --lms-db, the LMS history (play counts, ratings) follows moved files
(see lms.py). --lms-replay JOURNAL carries it over afterwards for one pass.

State (move journals, last pass date): --state-dir, else
$MUSIC_LIBRARIAN_STATE, else the state/ folder next to the script.

--root can also come from $MUSIC_LIBRARIAN_ROOT.

One --apply pass at a time (state/lock). On SIGTERM or Ctrl-C, the current
move completes and the rest is left for the next pass (second Ctrl-C: stop
at once).

Exit codes: 0 = OK, 2 = conflicts, skipped files or refused undos,
3 = another pass is running, 4 = stopped before the end, 5 = LMS carry-over failed.
"""
import argparse
import contextlib
import fcntl
import json
import os
import shlex
import shutil
import signal
import sys
import threading
import time
import unicodedata
from collections import defaultdict
from pathlib import Path

from mutagen import File as MFile
from mutagen.flac import FLAC
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4

import lms

AUDIO_EXT = {".m4a", ".mp3", ".flac"}
JUNK_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}
MIN_TRUNC = 20  # minimum length to accept a name truncated by Apple Music
LAST_RUN = "last_run"  # --since state file
# Taken off the start time stored in last_run: the kernel stamps ctime with a
# coarser clock than time.time(), a few ms behind, so a file changed right after
# the start would look older and the next --since would miss it. At worst
# those folders are read again.
SINCE_MARGIN = 2.0
LOCK = "lock"
EXIT_LOCKED, EXIT_STOPPED, EXIT_LMS = 3, 4, 5
STOP = threading.Event()  # stop requested (SIGTERM, Ctrl-C)
_lock_held = False  # pylint: disable=invalid-name  # process state


# ---------- helpers ----------

def nfc(s):
    return unicodedata.normalize("NFC", s)


def is_junk(p: Path):
    return p.name in JUNK_NAMES or p.name.startswith("._")


def first(v):
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        v = v[0] if v else None
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def to_int(x):
    try:
        return int(str(x).strip())
    except (TypeError, ValueError):
        return None


def parse_pair(s):
    """'3/12' -> (3, 12) ; '3' -> (3, None)"""
    if not s:
        return None, None
    parts = str(s).split("/")
    return to_int(parts[0]), (to_int(parts[1]) if len(parts) > 1 else None)


class Log:
    """stdout, plus journald (tag music-librarian) with --syslog."""

    def __init__(self, use_syslog=False, ident="music-librarian"):
        self.syslog = None
        self.ident = ident
        if use_syslog:
            import syslog  # pylint: disable=import-outside-toplevel  # only with --syslog
            self.syslog = syslog

    def __call__(self, msg=""):
        # invisible control characters (e.g. U+0092) shown escaped
        msg = "".join(f"\\x{ord(c):02x}" if unicodedata.category(c) == "Cc" and c != "\n"
                      else c for c in msg)
        print(msg, flush=True)   # in order with the subcommands' output
        if self.syslog and msg.strip():
            line = " ".join(part.strip() for part in msg.splitlines() if part.strip())
            # reopened on every line: import and range each have their own tag
            self.syslog.openlog(self.ident)
            # a non-UTF-8 file name (e.g. \x92) must not crash the log
            self.syslog.syslog(line.encode("utf-8", "backslashreplace").decode())


class Journal:
    """One JSON line per successful move, written right after it, so an
    interrupted pass can still be undone. The file is created on the first move."""

    def __init__(self, state_dir: Path, meta):
        self.state_dir = state_dir
        self.meta = meta
        self.path = None
        self.f = None

    def move(self, src, dst):
        if self.f is None:
            d = self.state_dir / "journal"
            d.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            for i in range(100):
                self.path = d / (f"{stamp}.jsonl" if i == 0 else f"{stamp}-{i}.jsonl")
                try:
                    # stays open for the whole pass, closed by close()
                    self.f = open(self.path, "x", encoding="utf-8")  # pylint: disable=consider-using-with
                    break
                except FileExistsError:
                    continue
            self._write(dict(type="meta", **self.meta))
        self._write(dict(type="move", src=str(src), dst=str(dst)))

    def _write(self, rec):
        # ensure_ascii: a non-UTF-8 name is kept as is (\udcXX) and read back identically
        self.f.write(json.dumps(rec) + "\n")
        self.f.flush()

    def close(self):
        if self.f:
            self.f.close()


def move_file(src: Path, dst: Path, journal: Journal):
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    journal.move(src, dst)


def lms_sync(db, moves, apply, log):
    """Carry the LMS history over to moved files; False on failure."""
    if db is None:
        return True
    pairs = db.pairs(moves, AUDIO_EXT)
    if not pairs:
        return True
    try:
        stats = db.sync(pairs, apply)
    except Exception as e:
        log(f"ERREUR   report LMS : {e}")
        return False
    for k, v in stats.items():
        if v:
            log(f"{'LMS ' + k:>20} : {v}")
    return True


def lms_replay(args, db, log):
    """--lms-replay: carry the LMS history over for the moves of a journal."""
    if db is None:
        log("ERREUR   --lms-replay demande --lms-db")
        return 2
    recs = [json.loads(line) for line in Path(args.lms_replay).read_text(
        encoding="utf-8").splitlines() if line.strip()]
    moves = [(r["src"], r["dst"]) for r in recs if r.get("type") == "move"]
    log(f"[{'APPLY' if args.apply else 'DRY-RUN'}] report LMS de {args.lms_replay}"
        f" ({len(moves)} déplacements)")
    return 0 if lms_sync(db, moves, args.apply, log) else EXIT_LMS


def prune_empty(d: Path, stops):
    """Remove d and its parents while they are empty, never reaching the
    folders in `stops` (root, folders passed as arguments)."""
    n = 0
    while d not in stops and any(d.is_relative_to(s) for s in stops):
        try:
            d.rmdir()
        except OSError:
            break
        n += 1
        d = d.parent
    return n


def state_path(arg=None):
    return Path(arg or os.environ.get("MUSIC_LIBRARIAN_STATE")
                or Path(__file__).resolve().parent / "state")


class Locked(Exception):
    pass


@contextlib.contextmanager
def locked(state_dir: Path):
    """Exclusive lock on state/lock: one --apply pass at a time (range, import,
    undo). Reentrant within a process: import calls range without blocking
    itself."""
    global _lock_held  # pylint: disable=global-statement
    if _lock_held:
        yield
        return
    state_dir.mkdir(parents=True, exist_ok=True)
    with open(state_dir / LOCK, "a", encoding="utf-8") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Locked(state_dir / LOCK) from None
        _lock_held = True
        try:
            yield
        finally:
            _lock_held = False
            fcntl.flock(f, fcntl.LOCK_UN)


def handle_stop_signals():
    """SIGTERM (docker stop) and Ctrl-C: finish the current operation, then stop."""
    def stop(_signum, _frame):
        if STOP.is_set():
            raise KeyboardInterrupt  # second request: stop at once
        STOP.set()
        print("arrêt demandé : fin de l'opération en cours…", flush=True)

    for s in (signal.SIGTERM, signal.SIGINT):
        signal.signal(s, stop)


# ---------- tag reading ----------

def read_tags(path: Path):
    f = MFile(path)
    if f is None or f.tags is None:
        return None
    t = f.tags

    if isinstance(f, MP4):
        g = lambda k: first(t.get(k))
        trk = (t.get("trkn") or [(0, 0)])[0]
        dsk = (t.get("disk") or [(0, 0)])[0]
        cpil = t.get("cpil")
        if isinstance(cpil, list):
            cpil = cpil[0] if cpil else False
        return dict(
            albumartist=g("aART"), artist=g("\xa9ART"), album=g("\xa9alb"),
            title=g("\xa9nam"), track=trk[0] or None, disc=dsk[0] or None,
            disctotal=dsk[1] or None, compilation=bool(cpil),
        )

    if isinstance(f, MP3):
        g = lambda k: first(t[k].text) if k in t else None
        tr, _ = parse_pair(g("TRCK"))
        d, dt = parse_pair(g("TPOS"))
        return dict(
            albumartist=g("TPE2"), artist=g("TPE1"), album=g("TALB"),
            title=g("TIT2"), track=tr, disc=d, disctotal=dt,
            compilation=(g("TCMP") == "1"),
        )

    if isinstance(f, FLAC):
        g = lambda k: first(t.get(k))
        tr, _ = parse_pair(g("tracknumber"))
        d, dt = parse_pair(g("discnumber"))
        dt = dt or to_int(g("disctotal") or g("totaldiscs"))
        return dict(
            albumartist=g("albumartist") or g("album artist"), artist=g("artist"),
            album=g("album"), title=g("title"), track=tr, disc=d, disctotal=dt,
            compilation=(g("compilation") == "1"),
        )

    return None


# ---------- target path ----------

def clean(s, is_dir=False, lead=True):
    """`lead=False`: the leading `.` is kept, for a title preceded by the track
    number (Apple Music only replaces it at the start of the whole name)."""
    # characters Apple Music replaces
    # observed: / : ? " | ’ ´   — by analogy: ‘ “ ” * < > \
    for ch in '/:?"|*<>\\’‘“”´':
        s = s.replace(ch, "_")
    s = nfc(s).strip()
    if lead and s.startswith("."):
        s = "_" + s[1:]
    if is_dir and s.endswith("."):
        s = s[:-1] + "_"
    return s or "_"


def target_rel(tags, ext, comp_dir):
    if not tags.get("title"):
        return None
    if tags["compilation"]:
        top = comp_dir
    else:
        top = clean(tags["albumartist"] or tags["artist"] or "Unknown Artist", True)
    album = clean(tags["album"] or "Unknown Album", True)

    prefix = ""
    if tags["track"]:
        prefix = f"{tags['track']:02d} "
        if (tags["disctotal"] or 0) > 1 or (tags["disc"] or 0) > 1:
            prefix = f"{tags['disc'] or 1}-{tags['track']:02d} "
    title = clean(tags["title"], lead=not prefix)
    return Path(top) / album / f"{prefix}{title}{ext.lower()}"


def same_component(cur, tgt, strict_case):
    cur, tgt = nfc(cur), nfc(tgt)
    if not strict_case:
        cur, tgt = cur.casefold(), tgt.casefold()
    if cur == tgt:
        return True
    # Apple Music sometimes truncates long names: a prefix is accepted
    return len(cur) >= MIN_TRUNC and tgt.startswith(cur)


def is_in_place(cur_rel: Path, tgt_rel: Path, strict_case):
    if len(cur_rel.parts) != len(tgt_rel.parts):
        return False
    for c, t in zip(cur_rel.parts[:-1], tgt_rel.parts[:-1]):
        if not same_component(c, t, strict_case):
            return False
    if cur_rel.suffix.lower() != tgt_rel.suffix.lower():
        return False
    return same_component(cur_rel.stem, tgt_rel.stem, strict_case)


# ---------- parcours ----------

def changed_since(p: Path, since):
    try:
        return os.lstat(p).st_ctime > since
    except OSError:
        return False


def collect(scan, since=None):
    """Files to examine. With `since`, only the folders where at least one file
    changed since then (ctime: a rewritten tag or a rename updates it), but then
    the whole folder, so side files never leave with only some of the tracks."""
    for base in scan:
        if base.is_file():           # single file passed as an argument
            yield base
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            paths = [Path(dirpath) / n for n in sorted(filenames)]
            if since is not None and not any(changed_since(p, since) for p in paths):
                continue
            yield from paths


# ---------- undo ----------

def undo(args, root, state_dir, log, db=None):
    jpath = Path(args.undo)
    recs = [json.loads(line) for line in jpath.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    moves = [(Path(r["dst"]), Path(r["src"])) for r in reversed(recs)
             if r.get("type") == "move"]
    mode = "APPLY" if args.apply else "DRY-RUN"
    log(f"[{mode}] annulation de {jpath} ({len(moves)} déplacements)")
    stopped = False

    todo, refused = [], 0
    for cur, orig in moves:
        if not cur.exists():
            log(f"REFUSÉ   (fichier absent)  {cur}")
            refused += 1
        elif orig.exists():
            log(f"REFUSÉ   (place d'origine prise)  {orig}")
            refused += 1
        else:
            log(f"RESTAURER {cur}\n          → {orig}")
            todo.append((cur, orig))

    journal = Journal(state_dir, dict(root=str(root), undo_of=str(jpath),
                                      started=time.time()))
    pruned = 0
    done = []
    if args.apply:
        for i, (cur, orig) in enumerate(todo):
            if STOP.is_set():
                log(f"ARRÊT    demandé : {len(todo) - i} restauration(s) non faite(s)")
                stopped = True
                break
            move_file(cur, orig, journal)
            done.append((cur, orig))
        for d in sorted({cur.parent for cur, _ in todo}, key=lambda d: -len(d.parts)):
            pruned += prune_empty(d, {root})
    journal.close()

    log("\n--- Résumé ---")
    log(f"{'à restaurer':>20} : {len(todo)}")
    if refused:
        log(f"{'refusés':>20} : {refused}")
    if pruned:
        log(f"{'dossiers supprimés':>20} : {pruned}")
    if journal.path:
        log(f"{'journal':>20} : {journal.path}")
    lms_ok = lms_sync(db, done if args.apply else todo, args.apply, log)
    if not lms_ok and journal.path:
        log(f"{'reprise LMS':>20} : music-librarian range --lms-replay "
            f"{shlex.quote(str(journal.path))} --apply")
    return EXIT_STOPPED if stopped else 2 if refused else 0 if lms_ok else EXIT_LMS


# ---------- main ----------

def main(argv=None):
    return run(argv)[0]


def run(argv=None, interruptible=True):
    """Like main, but returns (code, report): report["moves"] lists the planned
    moves, report["done"] the ones carried out.
    interruptible=False: a stop request does not interrupt the moves (import
    files an item entirely or not at all)."""
    ap = argparse.ArgumentParser(description="Range la musique façon Apple Music.")
    root_env = os.environ.get("MUSIC_LIBRARIAN_ROOT")
    ap.add_argument("--root", default=root_env, required=not root_env,
                    help="racine de la bibliothèque (défaut : $MUSIC_LIBRARIAN_ROOT)")
    ap.add_argument("paths", nargs="*", help="dossiers à traiter (défaut : --root)")
    ap.add_argument("--apply", action="store_true", help="exécuter (sinon dry-run)")
    ap.add_argument("--compilations-dir", default="Compilations")
    ap.add_argument("--strict-case", action="store_true",
                    help="renommer aussi les différences de casse seules")
    ap.add_argument("--since", action="store_true",
                    help="ne traiter que les dossiers modifiés depuis le dernier passage")
    ap.add_argument("--undo", metavar="JOURNAL", help="annuler les déplacements d'un journal")
    ap.add_argument("--lms-replay", metavar="JOURNAL",
                    help="reporter dans LMS l'historique des déplacements d'un journal")
    lms.add_arguments(ap)
    ap.add_argument("--state-dir", default=os.environ.get("MUSIC_LIBRARIAN_STATE"),
                    help="dossier d'état (journaux, dernier passage)")
    ap.add_argument("--syslog", action="store_true",
                    help="envoyer aussi les lignes dans syslog/journald (tag music-librarian)")
    args = ap.parse_args(argv)

    # a non-UTF-8 file name (e.g. \x92) must not crash the output
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    log = Log(args.syslog)
    root = Path(args.root).resolve()
    state_dir = state_path(args.state_dir)
    db = lms.from_args(args, root, state_dir)

    try:
        with locked(state_dir) if args.apply else contextlib.nullcontext():
            if args.lms_replay:
                return lms_replay(args, db, log), {}
            if args.undo:
                return undo(args, root, state_dir, log, db), {}
            return rank(args, argv, root, state_dir, log, interruptible, db)
    except Locked as e:
        log(f"VERROU   un autre passage est en cours ({e}), abandon")
        return EXIT_LOCKED, {}


def rank(args, argv, root, state_dir, log, interruptible, db=None):
    started = time.time()
    scan = [Path(p).resolve() for p in args.paths] or [root]
    mode = "APPLY" if args.apply else "DRY-RUN"
    log(f"[{mode}] racine : {root}")

    since = None
    if args.since:
        try:
            since = float((state_dir / LAST_RUN).read_text().strip())
            log(f"depuis le {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(since))}")
        except (OSError, ValueError):
            log("pas de dernier passage connu : tout est traité")

    entries = []                  # (file, tags, relative target path)
    stats = defaultdict(int)
    junk = []
    blocked = set()               # folders whose side files must not move

    for p in collect(scan, since):
        if is_junk(p):
            junk.append(p)
            continue
        if p.suffix.lower() not in AUDIO_EXT:
            continue
        stats["analysés"] += 1
        try:
            tags = read_tags(p)
        except Exception as e:
            log(f"IGNORÉ   (lecture impossible : {e})  {p}")
            stats["ignorés"] += 1
            blocked.add(p.parent)
            continue
        tgt = target_rel(tags, p.suffix, args.compilations_dir) if tags else None
        if tgt is None:
            log(f"IGNORÉ   (tags insuffisants)  {p}")
            stats["ignorés"] += 1
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
            log(f"CONFLIT  (cpil incohérent : {n}/{len(grp)} pistes en compilation)"
                f"  {d}  [{grp[0][1]['album']}]")
            stats["conflits"] += len(grp)
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

        if cur_rel is not None and is_in_place(cur_rel, tgt, args.strict_case):
            stats["en place"] += 1
            dir_targets[p.parent].add(p.parent)
            continue

        dst = root / tgt
        key = nfc(str(dst)).casefold()
        if key in seen_targets or (dst.exists() and not dst.samefile(p)):
            other = seen_targets.get(key, dst)
            log(f"CONFLIT  {p}\n         → {dst}\n         (déjà pris par {other})")
            stats["conflits"] += 1
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
                    log(f"CONFLIT  (annexe déjà présente)  {dst}")
                    stats["conflits"] += 1
                else:
                    extra.append((p, dst))

    for src, dst in moves + extra:
        log(f"DÉPLACER {src}\n         → {dst}")

    journal = Journal(state_dir, dict(root=str(root), started=started,
                                      argv=sys.argv[1:] if argv is None else argv))
    done = []
    stopped = False
    if args.apply:
        touched = set()
        for i, (src, dst) in enumerate(moves + extra):
            if interruptible and STOP.is_set():
                log(f"ARRÊT    demandé : {len(moves) + len(extra) - i} déplacement(s) non faits,"
                    " repris au prochain passage")
                stopped = True
                break
            if dst.exists() and not dst.samefile(src):
                log(f"CONFLIT  (apparu pendant le passage)  {dst}")
                stats["conflits"] += 1
                continue
            move_file(src, dst, journal)
            done.append((src, dst))
            stats["déplacés"] += 1
            touched.add(src.parent)
        for j in junk:
            j.unlink(missing_ok=True)
            touched.add(j.parent)
        # emptied folders (never the root nor the folders passed as arguments)
        protected = {root, *scan}
        for d in sorted(touched, key=lambda d: -len(d.parts)):
            stats["dossiers supprimés"] += prune_empty(d, protected)
        # The last pass only moves forward after a pass over the whole root.
        # Remaining conflicts do not hold it back: they show up again on the
        # next full pass, or as soon as their folder is touched.
        if scan == [root] and not stopped:
            state_dir.mkdir(parents=True, exist_ok=True)
            (state_dir / LAST_RUN).write_text(f"{started - SINCE_MARGIN}\n")
    journal.close()

    stats["à déplacer"] = len(moves)
    stats["annexes à déplacer"] = len(extra)
    stats["fichiers parasites"] = len(junk)
    log("\n--- Résumé ---")
    for k in ("analysés", "en place", "à déplacer", "annexes à déplacer", "déplacés",
              "ignorés", "conflits", "fichiers parasites", "dossiers supprimés"):
        if stats[k]:
            log(f"{k:>20} : {stats[k]}")
    lms_ok = lms_sync(db, done if args.apply else moves, args.apply, log)
    if journal.path:
        log(f"{'journal':>20} : {journal.path}")
        log(f"{'annuler':>20} : music-librarian undo {shlex.quote(str(journal.path))} "
            f"--root {shlex.quote(str(root))} --apply")
        if not lms_ok:
            log(f"{'reprise LMS':>20} : music-librarian range --lms-replay "
                f"{shlex.quote(str(journal.path))} --apply")

    code = EXIT_STOPPED if stopped else 2 if stats["conflits"] or stats["ignorés"] else 0
    if code == 0 and not lms_ok:
        code = EXIT_LMS
    return code, dict(moves=moves + extra, done=done, journal=journal.path)


if __name__ == "__main__":
    handle_stop_signals()
    sys.exit(main())
