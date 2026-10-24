"""State folder: move journals, last pass dates and lock."""
import os
from pathlib import Path


def state_path(arg=None):
    xdg = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    return Path(arg or os.environ.get("MUSIC_LIBRARIAN_STATE") or Path(xdg) / "music-librarian")


def read_stamp(state_dir: Path, name):
    """The time stored in state/name, or None if there is none."""
    try:
        return float((state_dir / name).read_text().strip())
    except (OSError, ValueError):
        return None


def write_stamp(state_dir: Path, name, t):
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / name).write_text(f"{t}\n")
