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
"""
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from urllib.parse import quote

from music_librarian.core.files import AUDIO_EXT

BACKUPS, KEEP_BACKUPS = "lyrion-backups", 14
# characters Lyrion leaves as is in its URLs (checked over the whole database)
URL_SAFE = "-_.!~*()/:@&=+$,[]"
BUSY_TIMEOUT = 60  # seconds to wait if Lyrion is writing at the same time
EXIT_LYRION = 5  # a pass whose carry-over failed

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


def from_args(args, root: Path, state_dir: Path):
    if not args.lyrion_db:
        return None
    if not args.lyrion_root:
        raise SystemExit("--lyrion-db demande --lyrion-root (ou $LYRION_MUSIC_ROOT)")
    return Lyrion(Path(args.lyrion_db), args.lyrion_root, root, state_dir / BACKUPS)


class Lyrion:
    def __init__(self, db: Path, lyrion_root, root: Path, backup_dir: Path):
        self.db, self.lyrion_root, self.root = db, os.fsencode(lyrion_root).rstrip(b"/"), root
        self.backup_dir = backup_dir

    def url(self, path: Path):
        """Lyrion URL for a library file; None if it is outside --root."""
        try:
            rel = Path(path).relative_to(self.root)
        except ValueError:
            return None
        return "file://" + quote(self.lyrion_root + b"/" + os.fsencode(rel), safe=URL_SAFE)

    def pairs(self, moves, audio_ext):
        out = []
        for src, dst in moves:
            if Path(src).suffix.lower() not in audio_ext:
                continue
            old, new = self.url(src), self.url(dst)
            if old and new and old != new:
                out.append((old, new))
        return out

    def _connect(self, readonly):
        if readonly:
            con = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True, timeout=BUSY_TIMEOUT)
        else:
            con = sqlite3.connect(self.db, timeout=BUSY_TIMEOUT, isolation_level=None)
        con.row_factory = sqlite3.Row
        return con

    def backup(self):
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        dest = self.backup_dir / f"persist-{time.strftime('%Y%m%d-%H%M%S')}.db"
        src, out = self._connect(readonly=True), sqlite3.connect(dest)
        try:
            src.backup(out)
        finally:
            out.close()
            src.close()
        for old in sorted(self.backup_dir.glob("persist-*.db"))[:-KEEP_BACKUPS]:
            old.unlink()
        return dest

    def sync(self, pairs, apply):
        """Carry the history over; returns {"renamed": n, "merged": n, ...}.
        In a dry run nothing is written (the database is opened read-only)."""
        stats = dict.fromkeys(("renamed", "merged", "no history"), 0)
        if not pairs:
            return stats
        if apply:
            stats["backup"] = self.backup()
        con = self._connect(readonly=not apply)
        try:
            if apply:
                con.execute("BEGIN IMMEDIATE")  # Lyrion cannot insert in between
            for old, new in pairs:
                kinds = {self._move(con, t, old, new, apply) for t in TABLES}
                kinds.discard(None)
                key = ("merged" if "merge" in kinds else
                       "renamed" if kinds else "no history")
                stats[key] += 1
            if apply:
                con.execute("COMMIT")
        except BaseException:
            if apply and con.in_transaction:
                con.execute("ROLLBACK")
            raise
        finally:
            con.close()
        return stats

    @staticmethod
    def _move(con, table, old, new, apply):
        md5_old, md5_new = md5(old), md5(new)
        row = con.execute(f"SELECT * FROM {table} WHERE urlmd5 = ?", (md5_old,)).fetchone()
        if row is None:
            return None
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


def carry_over(db, moves, apply, log):
    """Carry the Lyrion history over to moved files; False on failure."""
    if db is None:
        return True
    pairs = db.pairs(moves, AUDIO_EXT)
    if not pairs:
        return True
    try:
        stats = db.sync(pairs, apply)
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
