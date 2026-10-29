"""Run history: one row per pass in state/runs.db, and the pass's output in state/runs/<id>.log."""
import os
import re
import socket
import sqlite3
import time
import traceback
from pathlib import Path

from music_librarian.core.lock import EXIT_LOCKED, EXIT_STOPPED

FILE = "runs.db"
LOGS = "runs"
KEEP = 200
SECTION = re.compile(r"^--- .* ---$")
ENTRY = re.compile(r"^\s*([^:]+?): (.+)$")
RUNNING, INTERRUPTED, OK, ISSUES, STOPPED, FAILED = (
    "running", "interrupted", "ok", "issues", "stopped", "failed")


def connect(state_dir: Path):
    state_dir.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(state_dir / FILE, timeout=60)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY AUTOINCREMENT, command TEXT, "
               "origin TEXT, apply INTEGER, started REAL, ended REAL, code INTEGER, summary TEXT, "
               "host TEXT, pid INTEGER)")
    # a runs.db of version 0.1 has no host nor pid
    columns = {row[1] for row in db.execute("PRAGMA table_info(runs)")}
    for name, kind in (("host", "TEXT"), ("pid", "INTEGER")):
        if name not in columns:
            db.execute(f"ALTER TABLE runs ADD COLUMN {name} {kind}")
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
        run_id = db.execute("INSERT INTO runs (command, origin, apply, started, host, pid) "
                            "VALUES (?, ?, ?, ?, ?, ?)",
                            (command, origin, int(apply), time.time(), socket.gethostname(),
                             os.getpid())).lastrowid
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


def gone(run):
    """True when the run's process is known to be dead: it ran on this host (this container)
    under a pid that no longer exists. A run of another host is taken as alive."""
    if run["host"] != socket.gethostname() or run["pid"] is None:
        return False
    try:
        os.kill(run["pid"], 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        pass
    return False


def close_stale(state_dir: Path):
    """Mark as interrupted the unfinished runs of this host that no process carries on, for
    watch to call as it starts: its pid may be the one of the watch killed before it."""
    if not (state_dir / FILE).exists():
        return
    db = connect(state_dir)
    try:
        db.row_factory = sqlite3.Row
        stale = [r["id"] for r in db.execute("SELECT * FROM runs WHERE ended IS NULL")
                 if r["host"] is None or gone(r)
                 or (r["host"] == socket.gethostname() and r["pid"] == os.getpid())]
        with db:
            db.executemany("UPDATE runs SET ended = started WHERE id = ?", [(i,) for i in stale])
    finally:
        db.close()


def status(run):
    """RUNNING, INTERRUPTED (killed, crashed), or the outcome of its exit code."""
    if run["ended"] is None:
        return INTERRUPTED if gone(run) else RUNNING
    if run["code"] is None:
        return INTERRUPTED
    return {0: OK, 2: ISSUES, EXIT_STOPPED: STOPPED}.get(run["code"], FAILED)


def _select(state_dir: Path, where="", args=(), limit=None):
    if not (state_dir / FILE).exists():
        return []
    db = connect(state_dir)
    try:
        db.row_factory = sqlite3.Row
        rows = db.execute(f"SELECT * FROM runs {where} ORDER BY id DESC"
                          + (" LIMIT ?" if limit else ""), (*args, *([limit] if limit else []))).fetchall()
    finally:
        db.close()
    return [dict(r, apply=bool(r["apply"]), summary=(r["summary"] or "").splitlines()) for r in rows]


def recent(state_dir: Path, limit=20, command=None):
    """The latest runs, newest first, of one command or all: dicts of the runs table's columns,
    `summary` as a list of lines. `ended` and `code` are None while a run is going on, `code`
    alone for a pass that crashed."""
    if command:
        return _select(state_dir, "WHERE command = ?", (command,), limit)
    return _select(state_dir, limit=limit)


def get(state_dir: Path, run_id):
    """The run of that id as recent() returns it, or None."""
    rows = _select(state_dir, "WHERE id = ?", (run_id,))
    return rows[0] if rows else None


def latest(state_dir: Path):
    """{command: its latest run}."""
    return {r["command"]: r for r in
            _select(state_dir, "WHERE id IN (SELECT MAX(id) FROM runs GROUP BY command)")}


def read_log(state_dir: Path, run_id, offset=0):
    """(complete lines of the run's log from byte `offset`, offset after them); a line still
    being written is left for the next read."""
    try:
        with open(log_path(state_dir, run_id), "rb") as f:
            f.seek(offset)
            data = f.read()
    except OSError:
        return [], offset
    end = data.rfind(b"\n") + 1
    return data[:end].decode("utf-8", "backslashreplace").splitlines(), offset + end
