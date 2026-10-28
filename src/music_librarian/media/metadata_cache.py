"""Each music file's metadata (tags.read_snapshot) kept in metadata.db in the state folder,
so a pass only reads the files whose size, mtime or ctime changed.

The cache is state, not the library: dry runs write it too.
"""
import json
import os
import sqlite3
from pathlib import Path

from music_librarian.media import tags

FILE = "metadata.db"
VERSION = 1  # bumped when read_snapshot returns other fields
COMMIT_EVERY = 200


class MetadataCache:
    """Use as a context manager: what was read is committed on exit, even on an error."""

    def __init__(self, state_dir: Path):
        Path(state_dir).mkdir(parents=True, exist_ok=True)
        # paths are stored as bytes: a non-UTF-8 file name is a valid key too
        self.db = sqlite3.connect(Path(state_dir) / FILE, timeout=60)
        self.db.execute("PRAGMA journal_mode=WAL")
        # ctime too: a tagger can restore the mtime of a file whose size did not change
        self.db.execute("CREATE TABLE IF NOT EXISTS files (path BLOB PRIMARY KEY, mtime_ns INTEGER, "
                        "ctime_ns INTEGER, size INTEGER, version INTEGER, snapshot TEXT)")
        self.read_from_disk = self.from_cache = self.pending = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def snapshot(self, path):
        """tags.read_snapshot(path), from the cache when the file is unchanged; None if unreadable."""
        key = os.fsencode(os.path.abspath(path))
        try:
            st = os.stat(key)
        except OSError:
            return None
        stamp = (st.st_mtime_ns, st.st_ctime_ns, st.st_size, VERSION)
        row = self.db.execute("SELECT mtime_ns, ctime_ns, size, version, snapshot FROM files "
                              "WHERE path = ?", (key,)).fetchone()
        if row and row[:4] == stamp:
            self.from_cache += 1
            return json.loads(row[4])
        snap = tags.read_snapshot(path)
        self.db.execute("INSERT OR REPLACE INTO files VALUES (?, ?, ?, ?, ?, ?)",
                        (key, *stamp, json.dumps(snap)))
        self.read_from_disk += 1
        self.pending += 1
        if self.pending >= COMMIT_EVERY:
            self.commit()
        return snap

    def prune(self, root, files):
        """Drop the files under `root` that are not in `files` (moved or deleted); return how many."""
        prefix = os.fsencode(os.path.join(os.path.abspath(root), ""))
        keep = {os.fsencode(os.path.abspath(f)) for f in files}
        gone = [(k,) for (k,) in self.db.execute("SELECT path FROM files")
                if k.startswith(prefix) and k not in keep]
        self.db.executemany("DELETE FROM files WHERE path = ?", gone)
        self.commit()
        return len(gone)

    def commit(self):
        self.db.commit()
        self.pending = 0

    def close(self):
        try:
            self.commit()
        finally:
            self.db.close()


def genres(snap):
    """The non-empty genre values of a snapshot, [] for an unreadable file."""
    return [v.strip() for v in (snap or {}).get("tags", {}).get("genre", []) if v.strip()]


def metadata(snap):
    """{artist, title, album, duration} of a snapshot, or None for an unreadable file."""
    if snap is None:
        return None

    def first(key):
        values = snap["tags"].get(key)
        return values[0] if values else None

    return {"artist": first("artist"), "title": first("title"), "album": first("album"),
            "duration": snap["duration"]}
