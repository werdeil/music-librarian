"""Journals: what a pass moved or retagged, so it can be undone."""
import json
import shutil
import time
from pathlib import Path


class Journal:
    """One JSON line per successful move or tag change, written right after it, so
    an interrupted pass can still be undone. The file is created on the first one."""

    def __init__(self, state_dir: Path, meta):
        self.state_dir = state_dir
        self.meta = meta
        self.path = None
        self.f = None

    def move(self, src, dst):
        self._write(dict(type="move", src=str(src), dst=str(dst)))

    def tag(self, path, field, old, new):
        self._write(dict(type="tag", path=str(path), field=field, old=old, new=new))

    def _write(self, rec):
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
            self._append(dict(type="meta", **self.meta))
        self._append(rec)

    def _append(self, rec):
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
