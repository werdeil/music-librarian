"""Lyrics lookups that found nothing, kept in lyrics-misses.db in the state folder.

A file is looked up again once LYRICS_RETRY_DAYS (default 30, 0 to always look up)
have passed, or as soon as its artist, title or album changes. A lookup that reached
no provider is never recorded: it says nothing about what the providers carry.
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


def _query(meta):
    return json.dumps([meta.get("artist"), meta.get("title"), meta.get("album")])


class Misses:
    """Use as a context manager. Entries older than the retry delay are dropped on opening."""

    def __init__(self, state_dir: Path, days=None, now=time.time):
        self.cutoff = now() - (retry_days() if days is None else days) * 86400
        self.now = now
        Path(state_dir).mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(Path(state_dir) / FILE, timeout=60)
        self.db.execute("CREATE TABLE IF NOT EXISTS misses (path BLOB PRIMARY KEY, query TEXT, "
                        "checked_at REAL)")
        self.db.execute("DELETE FROM misses WHERE checked_at <= ?", (self.cutoff,))
        self.db.commit()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.db.close()

    def recent(self, path, meta):
        """True if `path`, with these tags, was looked up in vain within the retry delay."""
        row = self.db.execute("SELECT query, checked_at FROM misses WHERE path = ?",
                              (_key(path),)).fetchone()
        return bool(row) and row[0] == _query(meta) and row[1] > self.cutoff

    def record(self, path, meta):
        self.db.execute("INSERT OR REPLACE INTO misses VALUES (?, ?, ?)",
                        (_key(path), _query(meta), self.now()))
        self.db.commit()

    def forget(self, path):
        self.db.execute("DELETE FROM misses WHERE path = ?", (_key(path),))
        self.db.commit()


def _key(path):
    return os.fsencode(os.path.abspath(path))
