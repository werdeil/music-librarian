"""Lyrics lookups that found nothing, kept in lyrics-misses.db in the state folder.

A file is looked up again once LYRICS_RETRY_DAYS (default 30, 0 to always look up)
have passed, or as soon as its artist, title or album changes. A lookup that reached
no provider is never recorded: it says nothing about what the providers carry.
A file a provider called instrumental is kept until its artist, title or album changes.
"""
import json
import os
import sqlite3
import time
from pathlib import Path

FILE = "lyrics-misses.db"
DEFAULT_DAYS = 30


def retry_days():
    try:
        return max(0.0, float(os.environ.get("LYRICS_RETRY_DAYS", DEFAULT_DAYS)))
    except ValueError:
        return DEFAULT_DAYS


def query(meta):
    """What a record is kept for: the artist, title and album of `meta`."""
    return json.dumps([meta.get("artist"), meta.get("title"), meta.get("album")])


class Misses:
    """Use as a context manager. Entries older than the retry delay are dropped on opening."""

    def __init__(self, state_dir: Path, days=None, now=time.time):
        self.cutoff = now() - (retry_days() if days is None else days) * 86400
        self.now = now
        Path(state_dir).mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(Path(state_dir) / FILE, timeout=60)
        self.db.execute("CREATE TABLE IF NOT EXISTS misses (path BLOB PRIMARY KEY, query TEXT, "
                        "checked_at REAL, instrumental INTEGER NOT NULL DEFAULT 0)")
        if "instrumental" not in {row[1] for row in self.db.execute("PRAGMA table_info(misses)")}:
            self.db.execute("ALTER TABLE misses ADD COLUMN instrumental INTEGER NOT NULL DEFAULT 0")
        self.db.execute("DELETE FROM misses WHERE checked_at <= ? AND NOT instrumental", (self.cutoff,))
        self.db.commit()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.db.close()

    def _row(self, path, meta):
        row = self.db.execute("SELECT query, checked_at, instrumental FROM misses WHERE path = ?",
                              (_key(path),)).fetchone()
        return row if row and row[0] == query(meta) else None

    def recent(self, path, meta):
        """True if `path`, with these tags, was looked up in vain within the retry delay."""
        row = self._row(path, meta)
        return bool(row) and not row[2] and row[1] > self.cutoff

    def instrumental(self, path, meta):
        """True if a provider called `path`, with these tags, instrumental."""
        row = self._row(path, meta)
        return bool(row) and bool(row[2])

    def record(self, path, meta, instrumental=False):
        self.db.execute("INSERT OR REPLACE INTO misses (path, query, checked_at, instrumental) "
                        "VALUES (?, ?, ?, ?)", (_key(path), query(meta), self.now(), int(instrumental)))
        self.db.commit()

    def forget(self, path):
        self.db.execute("DELETE FROM misses WHERE path = ?", (_key(path),))
        self.db.commit()


def _key(path):
    return os.fsencode(os.path.abspath(path))


def _read(state_dir: Path, sql, *params):
    path = Path(state_dir) / FILE
    if not path.exists():
        return []
    db = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=60)
    try:
        columns = {row[1] for row in db.execute("PRAGMA table_info(misses)")}
        if "instrumental" not in columns:
            sql = sql.replace("instrumental", "0")
        return db.execute(sql, params).fetchall()
    finally:
        db.close()


def recorded(state_dir: Path, days=None, now=time.time):
    """{path: time of its last lookup} of the files looked up in vain within the retry delay,
    instrumentals aside, read without changing the file."""
    cutoff = now() - (retry_days() if days is None else days) * 86400
    rows = _read(state_dir, "SELECT path, checked_at FROM misses WHERE checked_at > ? AND NOT instrumental",
                 cutoff)
    return {Path(os.fsdecode(key)): at for key, at in rows}


def instrumentals(state_dir: Path):
    """{path: query of its tags then} of the files a provider called instrumental, read without
    changing the file."""
    return {Path(os.fsdecode(key)): tags
            for key, tags in _read(state_dir, "SELECT path, query FROM misses WHERE instrumental")}
