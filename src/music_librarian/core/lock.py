"""One --apply pass at a time, and stopping cleanly on SIGTERM or Ctrl-C."""
import contextlib
import fcntl
import signal
import threading
from pathlib import Path

LOCK = "lock"
EXIT_LOCKED, EXIT_STOPPED = 3, 4
STOP = threading.Event()  # stop requested (SIGTERM, Ctrl-C)


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


def handle_stop_signals():
    """SIGTERM (docker stop) and Ctrl-C: finish the current operation, then stop."""
    def stop(_signum, _frame):
        if STOP.is_set():
            raise KeyboardInterrupt  # second request: stop at once
        STOP.set()
        print("stop requested: finishing the current operation…", flush=True)

    for s in (signal.SIGTERM, signal.SIGINT):
        signal.signal(s, stop)
