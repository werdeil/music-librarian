"""The helpers that shape the output lines: counts and paths."""
from pathlib import Path

from music_librarian.core.files import relative
from music_librarian.core.log import count


def test_count():
    assert (count(0, "track"), count(1, "track"), count(2, "track")) == ("0 tracks", "1 track", "2 tracks")
    assert count(3, "batch", "batches") == "3 batches"


def test_relative():
    root = Path("/music")
    assert relative(root / "A/B/01.m4a", root) == "A/B/01.m4a"
    assert relative(Path("/inbox/x.m4a"), root) == "/inbox/x.m4a"
