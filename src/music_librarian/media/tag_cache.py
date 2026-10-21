"""Credit tags kept between passes in the state folder, so a pass only reads the files
whose size or modification time changed."""
import json
import os
from pathlib import Path

from music_librarian.media import credit_tags

FILE = "tag-cache.json"
VERSION = 1  # bumped when credit_tags.read returns other fields


class TagCache:
    def __init__(self, state_dir: Path):
        self.path = state_dir / FILE
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.entries = data["entries"] if data["version"] == VERSION else {}
        except (OSError, ValueError, KeyError, TypeError):
            self.entries = {}
        self.seen = set()

    def read(self, path: Path):
        """credit_tags.read(path), from the cache when the file is unchanged."""
        st = os.stat(path)
        stamp = [st.st_mtime_ns, st.st_size]
        name = str(path)
        self.seen.add(name)
        hit = self.entries.get(name)
        if hit and hit[:2] == stamp:
            return hit[2]
        tags = credit_tags.read(path)
        self.entries[name] = [*stamp, tags]
        return tags

    def save(self, full):
        """Write the cache; a full pass drops the files it did not see."""
        if full:
            self.entries = {k: v for k, v in self.entries.items() if k in self.seen}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        # ensure_ascii: a non-UTF-8 name is kept as is (\udcXX) and read back identically
        tmp.write_text(json.dumps({"version": VERSION, "entries": self.entries}), encoding="utf-8")
        os.replace(tmp, self.path)
