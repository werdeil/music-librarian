"""The PHYSICAL word of a comment, and what physical.json in the state folder keeps:
  "synced":    {album key: whether it was physical} as the last applied pass left both sides;
  "answers":   {pair key: true or false} whether a doubtful release is the album;
  "editions":  {album key: release id} the edition chosen to add an album to the collection;
  "conflicts": {album key: "add" or "drop"} for an album never synced, marked but not in the collection.
The dashboard writes the last three, the pass the first."""
import json
import os
import re
import threading
from pathlib import Path

FILE = "physical.json"
MARKER = "PHYSICAL"
WORD = re.compile(rf"(?<!\S){MARKER}(?!\S)")
KINDS = {"synced": bool, "answers": bool, "editions": str, "conflicts": str}
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
    """{name of KINDS: {key: value}}, each empty without a file."""
    try:
        found = json.loads((Path(state_dir) / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        found = {}
    found = found if isinstance(found, dict) else {}
    return {name: {k: v for k, v in (found.get(name) or {}).items() if isinstance(v, kind)}
            for name, kind in KINDS.items()}


def update(state_dir: Path, name, changes):
    """Write {key: value} into the part `name` of the file, a value None taking its key out."""
    path = Path(state_dir) / FILE
    with _lock:
        held = load(state_dir)
        for key, value in changes.items():
            if value is None:
                held[name].pop(key, None)
            else:
                held[name][key] = value
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".tmp")
        temp.write_text(json.dumps(held, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(temp, path)
