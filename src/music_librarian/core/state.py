"""State folder: move journals, last pass dates and lock."""
import os
from pathlib import Path


def state_path(arg=None):
    xdg = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    return Path(arg or os.environ.get("MUSIC_LIBRARIAN_STATE") or Path(xdg) / "music-librarian")
