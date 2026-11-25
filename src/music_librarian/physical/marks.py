"""The answers of the dashboard about the physical albums, in physical.json in the state folder:
{"manual": {album key: true or false}} the albums marked or unmarked by hand, which Discogs does
not override, and {"answers": {pair key: true or false}} whether a doubtful release is the album.
The marker itself, PHYSICAL, is a word of the comment of each track of a physical album."""
import json
import os
import re
import threading
from pathlib import Path

FILE = "physical.json"
MARKER = "PHYSICAL"
WORD = re.compile(rf"(?<!\S){MARKER}(?!\S)")
_lock = threading.Lock()


def marked(values):
    """Whether the comment values hold the marker."""
    return any(WORD.search(v) for v in values)


def with_marker(values):
    if marked(values):
        return list(values)
    return [f"{values[0]} {MARKER}", *values[1:]] if values else [MARKER]


def without_marker(values):
    out = (re.sub(rf"[ \t]?{WORD.pattern}", "", v).strip() for v in values)
    return [v for v in out if v]


def load(state_dir: Path):
    """(manual, answers), each {key: bool}, empty without a file."""
    try:
        found = json.loads((Path(state_dir) / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        found = {}
    pick = lambda name: {k: v for k, v in (found.get(name) or {}).items() if isinstance(v, bool)}
    return pick("manual"), pick("answers")


def _set(state_dir: Path, name, key, value):
    path = Path(state_dir) / FILE
    with _lock:
        manual, answers = load(state_dir)
        held = {"manual": manual, "answers": answers}
        if value is None:
            held[name].pop(key, None)
        else:
            held[name][key] = bool(value)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".tmp")
        temp.write_text(json.dumps(held, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(temp, path)


def set_manual(state_dir: Path, key, value):
    """Mark the album physical (True) or not (False) by hand; None gives it back to Discogs."""
    _set(state_dir, "manual", key, value)


def set_answer(state_dir: Path, key, value):
    """Answer whether a doubtful release is the album (True, False); None asks again."""
    _set(state_dir, "answers", key, value)
