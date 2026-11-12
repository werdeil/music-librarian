"""
lyrion — carries the history of moved files over in the Lyrion database (persist.db).

Lyrion keys play counts and ratings on the file URL (tables tracks_persistent and
alternativeplaycount, key urlmd5 = md5 of the URL). After a rename it creates a
fresh entry and the old one is left orphaned. For each move:
  - the old entry takes the new URL, if Lyrion has not seen the file yet;
  - otherwise its counters are merged into the new entry, then it is deleted.

Lyrion can stay up: SQLite writes in place, in one short transaction, after a
backup copy of the database (WAL included).

Settings: --lyrion-db / $LYRION_PERSIST_DB (off without it), --lyrion-root /
$LYRION_MUSIC_ROOT = the library root as Lyrion sees it.

With --lyrion-url / $LYRION_URL, an applied pass asks Lyrion whether it scans (JSON-RPC) and
waits for the end of the scan, before changing files and before the carry-over. A database
found locked is tried again a few times.
"""
import contextlib
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from urllib.parse import quote, unquote_to_bytes

import requests

from music_librarian.core.files import AUDIO_EXT
from music_librarian.core.lock import STOP

BACKUPS, KEEP_BACKUPS = "lyrion-backups", 14
# characters Lyrion leaves as is in its URLs (checked over the whole database)
URL_SAFE = "-_.!~*()/:@&=+$,[]"
BUSY_TIMEOUT = 60  # seconds to wait if Lyrion is writing at the same time
EXIT_LYRION = 5  # a pass whose carry-over failed
SCAN_POLL, SCAN_TIMEOUT = 5, 900  # seconds between two looks at a scan, and waited for its end
RPC_TIMEOUT = 10
LOCK_RETRIES = (5, 10, 20, 40)  # seconds waited before each new try of a locked database

TABLES = {
    # table: (summed columns, latest-value columns, columns filled in if empty)
    "tracks_persistent": (("playCount",), ("lastPlayed", "lastRated"),
                          ("rating", "prevRating", "musicbrainz_id")),
    "alternativeplaycount": (("playCount", "skipCount"), ("lastPlayed", "lastSkipped"),
                             ("musicbrainz_id",)),
}


def add_arguments(ap):
    ap.add_argument("--lyrion-db", default=os.environ.get("LYRION_PERSIST_DB"),
                    help="Lyrion's persist.db, to carry the history of moved files over "
                         "(default: $LYRION_PERSIST_DB, otherwise off)")
    ap.add_argument("--lyrion-root", default=os.environ.get("LYRION_MUSIC_ROOT"),
                    help="library root as Lyrion sees it (default: $LYRION_MUSIC_ROOT)")
    ap.add_argument("--lyrion-url", default=os.environ.get("LYRION_URL"), metavar="URL",
                    help="Lyrion's address (http://host:9000), to wait for the end of its scans "
                         "before changing files and before the carry-over "
                         "(default: $LYRION_URL, otherwise no waiting)")
    ap.add_argument("--lyrion-user", default=os.environ.get("LYRION_USER"), metavar="NAME",
                    help="user name when Lyrion asks for one, its password read from "
                         "$LYRION_PASSWORD (default: $LYRION_USER)")
    ap.add_argument("--scan-poll", type=float, metavar="SECONDS",
                    default=float(os.environ.get("LYRION_SCAN_POLL") or SCAN_POLL),
                    help=f"seconds between two looks at a scan of Lyrion's "
                         f"(default: $LYRION_SCAN_POLL, else {SCAN_POLL})")
    ap.add_argument("--scan-timeout", type=float, metavar="SECONDS",
                    default=float(os.environ.get("LYRION_SCAN_TIMEOUT") or SCAN_TIMEOUT),
                    help=f"seconds a scan of Lyrion's is waited for "
                         f"(default: $LYRION_SCAN_TIMEOUT, else {SCAN_TIMEOUT})")
    ap.add_argument("--no-wait-scan", action="store_true",
                    help="do not wait for the end of Lyrion's scans, --lyrion-url set or not")


def from_args(args, root: Path, state_dir: Path):
    if not args.lyrion_db:
        return None
    if not args.lyrion_root:
        raise SystemExit("--lyrion-db needs --lyrion-root (or $LYRION_MUSIC_ROOT)")
    scan = None
    if args.lyrion_url and not args.no_wait_scan:
        auth = (args.lyrion_user, os.environ.get("LYRION_PASSWORD", "")) if args.lyrion_user else None
        scan = Scan(args.lyrion_url, auth, args.scan_poll, args.scan_timeout)
    return Lyrion(Path(args.lyrion_db), args.lyrion_root, root, state_dir / BACKUPS, scan)


def pause(seconds):
    """Wait `seconds`, less once a stop is requested: True then."""
    return STOP.wait(seconds)


def duration(seconds):
    seconds = round(seconds)
    return f"{seconds // 60} min" if seconds >= 120 else f"{seconds} s"


def reason(error):
    if isinstance(error, requests.HTTPError):
        return f"HTTP {error.response.status_code}"
    if isinstance(error, requests.Timeout):
        return f"no answer in {RPC_TIMEOUT} s"
    if isinstance(error, requests.ConnectionError):
        return "connection failed"
    return "unexpected answer"


class Scan:
    """Lyrion's library scan, asked about over the JSON-RPC interface of the server at `url`;
    `auth` is (user, password) when Lyrion asks for a login."""

    def __init__(self, url, auth=None, poll=SCAN_POLL, timeout=SCAN_TIMEOUT):
        self.url, self.auth, self.poll, self.timeout = url.rstrip("/"), auth, poll, timeout

    def ask(self, *command):
        r = requests.post(f"{self.url}/jsonrpc.js", auth=self.auth, timeout=RPC_TIMEOUT,
                          json={"id": 1, "method": "slim.request", "params": ["", list(command)]})
        r.raise_for_status()
        answer = r.json()
        result = answer.get("result") if isinstance(answer, dict) else None
        if not isinstance(result, dict):
            raise ValueError(answer)
        return result

    def running(self):
        """Whether Lyrion is scanning; raises requests.RequestException or ValueError when it
        cannot be asked."""
        result = self.ask("rescan", "?")
        if "_rescan" in result:
            state = result["_rescan"]
        else:
            # serverstatus holds "rescan" during a scan only
            state = self.ask("serverstatus", 0, 0).get("rescan")
        return str(state or 0).strip() != "0"

    def wait(self, log):
        """Wait for the end of a scan; False when it still runs after `timeout` or once a stop
        is requested. Lyrion out of reach counts as not scanning, after a warning."""
        start, waiting = time.monotonic(), False
        while True:
            try:
                scanning = self.running()
            except (requests.RequestException, ValueError) as e:
                log(f"WARNING  Lyrion cannot be asked whether it scans ({reason(e)}): not waiting")
                return True
            waited = time.monotonic() - start
            if not scanning:
                if waiting:
                    log(f"LYRION   scan ended after {duration(waited)}")
                return True
            if not waiting:
                log(f"LYRION   scanning: waiting for the end, up to {duration(self.timeout)}")
                waiting = True
            if waited >= self.timeout:
                log(f"WARNING  Lyrion still scanning after {duration(waited)}: no longer waiting")
                return False
            if pause(min(self.poll, self.timeout - waited)):
                log("STOP     requested: no longer waiting for Lyrion's scan")
                return False


class Lyrion:
    # pylint: disable-next=too-many-arguments,too-many-positional-arguments
    def __init__(self, db: Path, lyrion_root, root: Path, backup_dir: Path, scan: Scan | None = None):
        self.db, self.lyrion_root, self.root = db, os.fsencode(lyrion_root).rstrip(b"/"), root
        self.backup_dir, self.scan = backup_dir, scan

    def scan_ended(self, log):
        """Wait for Lyrion's scan to end; False when it still runs. True without --lyrion-url."""
        return self.scan is None or self.scan.wait(log)

    def url(self, path: Path):
        """Lyrion URL for a library file; None if it is outside --root."""
        try:
            rel = Path(path).relative_to(self.root)
        except ValueError:
            return None
        return "file://" + quote(self.lyrion_root + b"/" + os.fsencode(rel), safe=URL_SAFE)

    def relative(self, url):
        """The path a Lyrion URL names, relative to the library when under Lyrion's root."""
        path = unquote_to_bytes(url.removeprefix("file://"))
        return os.fsdecode(path.removeprefix(self.lyrion_root + b"/"))

    def path(self, url):
        """The library file a Lyrion URL names, None for one outside Lyrion's root."""
        path = unquote_to_bytes(url.removeprefix("file://"))
        if not path.startswith(self.lyrion_root + b"/"):
            return None
        return self.root / os.fsdecode(path.removeprefix(self.lyrion_root + b"/"))

    def pairs(self, moves, audio_ext):
        out = []
        for src, dst in moves:
            if Path(src).suffix.lower() not in audio_ext:
                continue
            old, new = self.url(src), self.url(dst)
            if old and new and old != new:
                out.append((old, new))
        return out

    def connect(self, readonly=True):
        if readonly:
            con = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True, timeout=BUSY_TIMEOUT)
        else:
            con = sqlite3.connect(self.db, timeout=BUSY_TIMEOUT, isolation_level=None)
        con.row_factory = sqlite3.Row
        return con

    def backup(self):
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        dest = self.backup_dir / f"persist-{time.strftime('%Y%m%d-%H%M%S')}.db"
        src, out = self.connect(), sqlite3.connect(dest)
        try:
            src.backup(out)
        finally:
            out.close()
            src.close()
        for old in sorted(self.backup_dir.glob("persist-*.db"))[:-KEEP_BACKUPS]:
            old.unlink()
        return dest

    @contextlib.contextmanager
    def writing(self):
        """A connection inside one BEGIN IMMEDIATE transaction, so Lyrion cannot insert in between:
        committed on exit, rolled back on an error."""
        con = self.connect(readonly=False)
        try:
            con.execute("BEGIN IMMEDIATE")
            yield con
            con.execute("COMMIT")
        except BaseException:
            if con.in_transaction:
                con.execute("ROLLBACK")
            raise
        finally:
            con.close()

    def sync(self, pairs, apply):
        """Carry the history over; returns {"renamed": n, "merged": n, ...}.
        In a dry run nothing is written (the database is opened read-only)."""
        stats = dict.fromkeys(("renamed", "merged", "no history"), 0)
        if not pairs:
            return stats
        if apply:
            stats["backup"] = self.backup()
        try:
            with self.writing() if apply else contextlib.closing(self.connect()) as con:
                for old, new in pairs:
                    kinds = {self._move(con, t, old, new, apply) for t in TABLES}
                    kinds.discard(None)
                    key = ("merged" if "merge" in kinds else
                           "renamed" if kinds else "no history")
                    stats[key] += 1
        except BaseException:
            if apply:
                stats["backup"].unlink(missing_ok=True)
            raise
        return stats

    @staticmethod
    def _move(con, table, old, new, apply):
        row = con.execute(f"SELECT * FROM {table} WHERE urlmd5 = ?", (md5(old),)).fetchone()
        return None if row is None else carry(con, table, row, new, apply)


def carry(con, table, row, new, apply=True):
    """Give the entry `row` of `table` the URL `new`: "rename", or "merge" into the entry that
    URL already has, `row` then being deleted."""
    md5_old, md5_new = row["urlmd5"], md5(new)
    cur = con.execute(f"SELECT * FROM {table} WHERE urlmd5 = ?", (md5_new,)).fetchone()
    if cur is None:
        if apply:
            con.execute(f"UPDATE {table} SET url = ?, urlmd5 = ? WHERE urlmd5 = ?",
                        (new, md5_new, md5_old))
        return "rename"
    if apply:
        summed, latest, kept = TABLES[table]
        cols = row.keys()
        vals = {c: (row[c] or 0) + (cur[c] or 0) for c in summed if c in cols}
        vals |= {c: max(row[c] or 0, cur[c] or 0) or None for c in latest if c in cols}
        vals |= {c: cur[c] if cur[c] is not None else row[c] for c in kept if c in cols}
        if "added" in cols:
            vals["added"] = min((x for x in (row["added"], cur["added"]) if x is not None),
                                default=None)
        if "dynPSval" in cols:
            # the plugin's dynamic rating: the one of the entry with the most events
            ev = lambda r: (r["playCount"] or 0) + (r["skipCount"] or 0)
            vals["dynPSval"] = (cur if ev(cur) >= ev(row) else row)["dynPSval"]
        sets = ", ".join(f"{c} = ?" for c in vals)
        con.execute(f"UPDATE {table} SET {sets} WHERE urlmd5 = ?", (*vals.values(), md5_new))
        con.execute(f"DELETE FROM {table} WHERE urlmd5 = ?", (md5_old,))
    return "merge"


def md5(url):
    return hashlib.md5(url.encode()).hexdigest()


def await_scan(db, apply, log):
    """Before an applied pass changes files: wait for the end of Lyrion's scan, and go on
    whether it ended or not."""
    if db is not None and apply:
        db.scan_ended(log)


def write_history(db, pairs, log):
    """db.sync() applied once Lyrion's scan has ended, tried again while the database is locked."""
    delays = list(LOCK_RETRIES)
    while True:
        if not db.scan_ended(log):
            raise RuntimeError("not done while Lyrion scans")
        try:
            return db.sync(pairs, True)
        except sqlite3.OperationalError as e:
            if not delays or "locked" not in str(e):
                raise
            log(f"LYRION   {e}, trying again in {duration(delays[0])}")
            if pause(delays.pop(0)):
                raise


def carry_over(db, moves, apply, log):
    """Carry the Lyrion history over to moved files; False on failure."""
    if db is None:
        return True
    pairs = db.pairs(moves, AUDIO_EXT)
    if not pairs:
        return True
    try:
        stats = write_history(db, pairs, log) if apply else db.sync(pairs, apply)
    except Exception as e:
        log(f"ERROR    Lyrion carry-over: {e}")
        return False
    for k, v in stats.items():
        if v:
            log(f"{'Lyrion ' + k:>20}: {v}")
    return True


def replay(args, db, log):
    """--lyrion-replay: carry the Lyrion history over for the moves of a journal."""
    if db is None:
        log("ERROR    --lyrion-replay needs --lyrion-db")
        return 2
    recs = [json.loads(line) for line in Path(args.lyrion_replay).read_text(
        encoding="utf-8").splitlines() if line.strip()]
    moves = [(r["src"], r["dst"]) for r in recs if r.get("type") == "move"]
    log(f"[{'APPLY' if args.apply else 'DRY-RUN'}] Lyrion carry-over of {args.lyrion_replay}"
        f" ({len(moves)} moves)")
    return 0 if carry_over(db, moves, args.apply, log) else EXIT_LYRION
