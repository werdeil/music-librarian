"""Move journals: what a pass moved, so it can be undone."""
import json
import shutil
import time
from pathlib import Path


class Journal:
    """One JSON line per successful move, written right after it, so an
    interrupted pass can still be undone. The file is created on the first move."""

    def __init__(self, state_dir: Path, meta):
        self.state_dir = state_dir
        self.meta = meta
        self.path = None
        self.f = None

    def move(self, src, dst):
        if self.f is None:
            d = self.state_dir / "journal"
            d.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            for i in range(100):
                self.path = d / (f"{stamp}.jsonl" if i == 0 else f"{stamp}-{i}.jsonl")
                try:
                    # stays open for the whole pass, closed by close()
                    self.f = open(self.path, "x", encoding="utf-8")  # pylint: disable=consider-using-with
                    break
                except FileExistsError:
                    continue
            self._write(dict(type="meta", **self.meta))
        self._write(dict(type="move", src=str(src), dst=str(dst)))

    def _write(self, rec):
        # ensure_ascii: a non-UTF-8 name is kept as is (\udcXX) and read back identically
        self.f.write(json.dumps(rec) + "\n")
        self.f.flush()

    def close(self):
        if self.f:
            self.f.close()


def move_file(src: Path, dst: Path, journal: Journal):
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    journal.move(src, dst)
