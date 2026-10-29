"""Run history: one row per pass in state/runs.db, and the pass's output in state/runs/<id>.log."""
import re
import sqlite3
import time
import traceback
from pathlib import Path

from music_librarian.core.lock import EXIT_LOCKED

FILE = "runs.db"
LOGS = "runs"
KEEP = 200
SECTION = re.compile(r"^--- .* ---$")
ENTRY = re.compile(r"^\s*([^:]+?): (.+)$")


def connect(state_dir: Path):
    state_dir.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(state_dir / FILE, timeout=60)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY AUTOINCREMENT, command TEXT, "
               "origin TEXT, apply INTEGER, started REAL, ended REAL, code INTEGER, summary TEXT)")
    return db


def log_path(state_dir: Path, run_id):
    return state_dir / LOGS / f"{run_id}.log"


class Output:
    """Writes each line to the run's log file and keeps the `key: value` lines of the last
    `--- … ---` section that has any: the pass's summary."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(path, "w", encoding="utf-8", errors="backslashreplace")  # pylint: disable=consider-using-with
        self.summary, self.section = [], None

    def __call__(self, msg):
        self.f.write(msg + "\n")
        self.f.flush()
        for line in msg.splitlines():
            if SECTION.match(line.strip()):
                self.section = []
            elif self.section is not None and (m := ENTRY.match(line)):
                self.section.append(f"{m[1]}: {m[2]}")
                self.summary = self.section

    def close(self):
        self.f.close()


def recorded(fn, log, state_dir: Path, command, apply, origin="cli"):
    """fn()'s exit code, the pass being kept in the run history with what `log` prints meanwhile.

    A pass that found the lock taken did nothing and is not kept. The oldest runs beyond KEEP
    are dropped, their log files with them.
    """
    db = connect(state_dir)
    with db:
        run_id = db.execute("INSERT INTO runs (command, origin, apply, started) VALUES (?, ?, ?, ?)",
                            (command, origin, int(apply), time.time())).lastrowid
    out = Output(log_path(state_dir, run_id))
    previous, log.tee = log.tee, out
    code = None
    try:
        code = fn()
        return code
    except BaseException:
        out(f"ERROR    {command} interrupted:\n{traceback.format_exc()}")
        raise
    finally:
        log.tee = previous
        out.close()
        with db:
            if code == EXIT_LOCKED:
                db.execute("DELETE FROM runs WHERE id = ?", (run_id,))
                log_path(state_dir, run_id).unlink(missing_ok=True)
            else:
                db.execute("UPDATE runs SET ended = ?, code = ?, summary = ? WHERE id = ?",
                           (time.time(), code, "\n".join(out.summary), run_id))
            prune(db, state_dir)
        db.close()


def prune(db, state_dir: Path):
    old = [i for (i,) in db.execute("SELECT id FROM runs ORDER BY id DESC LIMIT -1 OFFSET ?", (KEEP,))]
    db.executemany("DELETE FROM runs WHERE id = ?", [(i,) for i in old])
    for i in old:
        log_path(state_dir, i).unlink(missing_ok=True)


def recent(state_dir: Path, limit=20):
    """The latest runs, newest first: dicts of the runs table's columns, `summary` as a list of lines.
    `ended` and `code` are None while a run is going on, `code` alone for a pass that crashed."""
    if not (state_dir / FILE).exists():
        return []
    db = connect(state_dir)
    try:
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    finally:
        db.close()
    return [dict(r, apply=bool(r["apply"]), summary=(r["summary"] or "").splitlines()) for r in rows]
