"""The matches file: for each orphan entry of Lyrion answered, the track that takes its history,
that it is ignored, or that it is to delete. Entries are keyed by the orphan's urlmd5."""
import json
import os
from pathlib import Path

FILE = "playcount-matches.json"


def load(path: Path):
    """({orphan's urlmd5: track's path relative to the library}, {urlmd5 ignored}, {urlmd5 to
    delete})."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, set(), set()
    return dict(data.get("matches", {})), set(data.get("ignored", ())), set(data.get("deleted", ()))


def update(path: Path, match=None, ignore=(), delete=(), drop=()):
    """Write the answers: `match` {urlmd5: relative path}, `ignore` and `delete` urlmd5s, and
    forget those of `drop`. An answer replaces the one its orphan had."""
    matches, ignored, deleted = load(path)
    match = match or {}
    for key in (*drop, *match, *ignore, *delete):
        matches.pop(key, None)
        ignored.discard(key)
        deleted.discard(key)
    matches |= match
    ignored |= set(ignore)
    deleted |= set(delete)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps({"matches": matches, "ignored": sorted(ignored),
                                "deleted": sorted(deleted)}, indent=1), encoding="utf-8")
    os.replace(temp, path)
