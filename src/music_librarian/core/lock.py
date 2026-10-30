"""One --apply pass at a time, and stopping cleanly on SIGTERM or Ctrl-C."""
import contextlib
import fcntl
import signal
import threading
from pathlib import Path

LOCK = "lock"
EXIT_LOCKED, EXIT_STOPPED = 3, 4
STOP = threading.Event()  # stop requested (SIGTERM, Ctrl-C, or for one pass)
SIGNALLED = threading.Event()  # by SIGTERM or Ctrl-C: the process itself stops
ONE_PASS = threading.Event()  # by stop_pass(): only the pass running stops


class Locked(Exception):
    pass


@contextlib.contextmanager
def locked(state_dir: Path):
    """Exclusive lock on state/lock: one --apply pass at a time (organize, import,
    undo, enrich). Not reentrant: a pass run from another one goes through its unlocked function."""
    state_dir.mkdir(parents=True, exist_ok=True)
    with open(state_dir / LOCK, "a", encoding="utf-8") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Locked(state_dir / LOCK) from None
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def stop_pass():
    """Stop the pass running as SIGTERM would, leaving the process to go on: see resume()."""
    ONE_PASS.set()
    STOP.set()


def resume():
    """After a pass stopped by stop_pass(), take the stop back, unless a signal came meanwhile.
    A loop running passes calls it before each look at STOP."""
    if ONE_PASS.is_set():
        ONE_PASS.clear()
        if not SIGNALLED.is_set():
            STOP.clear()


def handle_stop_signals():
    """SIGTERM (docker stop) and Ctrl-C: finish the current operation, then stop."""
    def stop(_signum, _frame):
        if SIGNALLED.is_set():
            raise KeyboardInterrupt  # second request: stop at once
        SIGNALLED.set()
        STOP.set()
        print("stop requested: finishing the current operation…", flush=True)

    for s in (signal.SIGTERM, signal.SIGINT):
        signal.signal(s, stop)
