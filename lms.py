"""
lms — carries the history of moved files over in the LMS database (persist.db).

LMS keys play counts and ratings on the file URL (tables tracks_persistent and
alternativeplaycount, key urlmd5 = md5 of the URL). After a rename it creates a
fresh entry and the old one is left orphaned. For each move:
  - the old entry takes the new URL, if LMS has not seen the file yet;
  - otherwise its counters are merged into the new entry, then it is deleted.

LMS can stay up: SQLite writes in place, in one short transaction, after a
backup copy of the database (WAL included).

Settings: --lms-db / $LMS_PERSIST_DB (off without it), --lms-root /
$LMS_MUSIC_ROOT = the library root as LMS sees it.
"""
import hashlib
import os
import sqlite3
import time
from pathlib import Path
from urllib.parse import quote

BACKUPS, KEEP_BACKUPS = "lms-backups", 14
# characters LMS leaves as is in its URLs (checked over the whole database)
URL_SAFE = "-_.!~*()/:@&=+$,[]"
BUSY_TIMEOUT = 60  # seconds to wait if LMS is writing at the same time

TABLES = {
    # table: (summed columns, latest-value columns, columns filled in if empty)
    "tracks_persistent": (("playCount",), ("lastPlayed", "lastRated"),
                          ("rating", "prevRating", "musicbrainz_id")),
    "alternativeplaycount": (("playCount", "skipCount"), ("lastPlayed", "lastSkipped"),
                             ("musicbrainz_id",)),
}


def add_arguments(ap):
    ap.add_argument("--lms-db", default=os.environ.get("LMS_PERSIST_DB"),
                    help="persist.db de LMS, pour y reporter l'historique des fichiers "
                         "déplacés (défaut : $LMS_PERSIST_DB, sinon désactivé)")
    ap.add_argument("--lms-root", default=os.environ.get("LMS_MUSIC_ROOT"),
                    help="racine de la bibliothèque vue par LMS (défaut : $LMS_MUSIC_ROOT)")


def from_args(args, root: Path, state_dir: Path):
    if not args.lms_db:
        return None
    if not args.lms_root:
        raise SystemExit("--lms-db demande --lms-root (ou $LMS_MUSIC_ROOT)")
    return Lms(Path(args.lms_db), args.lms_root, root, state_dir / BACKUPS)


class Lms:
    def __init__(self, db: Path, lms_root, root: Path, backup_dir: Path):
        self.db, self.lms_root, self.root = db, os.fsencode(lms_root).rstrip(b"/"), root
        self.backup_dir = backup_dir

    def url(self, path: Path):
        """LMS URL for a library file; None if it is outside --root."""
        try:
            rel = Path(path).relative_to(self.root)
        except ValueError:
            return None
        return "file://" + quote(self.lms_root + b"/" + os.fsencode(rel), safe=URL_SAFE)

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
        """Carry the history over; returns {"renommées": n, "fusionnées": n, ...}.
        In a dry run nothing is written (the database is opened read-only)."""
        stats = dict.fromkeys(("renommées", "fusionnées", "sans historique"), 0)
        if not pairs:
            return stats
        if apply:
            stats["sauvegarde"] = self.backup()
        con = self._connect(readonly=not apply)
        try:
            if apply:
                con.execute("BEGIN IMMEDIATE")  # LMS cannot insert in between
            for old, new in pairs:
                kinds = {self._move(con, t, old, new, apply) for t in TABLES}
                kinds.discard(None)
                key = ("fusionnées" if "fusion" in kinds else
                       "renommées" if kinds else "sans historique")
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
            return "renommage"
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
        return "fusion"


def md5(url):
    return hashlib.md5(url.encode()).hexdigest()
