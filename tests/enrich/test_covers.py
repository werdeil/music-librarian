"""enrich.covers: folder.jpg embedded into the tags, on real files."""
# pylint: disable=redefined-outer-name,unused-argument  # pytest fixtures
import struct

import pytest

from music_librarian.enrich import covers
from music_librarian.media import tags


def jpeg(width, height):
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 17, 8, height, width, 3)
    return b"\xff\xd8" + sof + b"\x01\x11\x00\x02\x11\x01\x03\x11\x01"


@pytest.fixture
def album(tmp_path, make_track):
    d = tmp_path / "Musique" / "Nirvana" / "MTV Unplugged [Live]"
    tracks = [make_track(d / f"0{i} Titre{ext}", title="Titre", album="MTV Unplugged")
              for i, ext in enumerate((".m4a", ".mp3", ".flac"), 1)]
    (d / "._01 Titre.m4a").write_bytes(b"AppleDouble")
    return d, tracks


def test_dry_run_writes_nothing(album, capsys):
    d, tracks = album
    (d / "folder.jpg").write_bytes(jpeg(600, 600))
    assert covers.main([str(d), "--dry-run"]) == 0
    assert "[would]" in capsys.readouterr().out
    assert all(tags.read_cover(str(t)) is None for t in tracks)


def test_written_then_up_to_date(album, capsys):
    d, tracks = album
    cover = jpeg(600, 600)
    (d / "Folder.JPG").write_bytes(cover)              # case-insensitive
    assert covers.main([str(d)]) == 0
    assert all(tags.read_cover(str(t)) == cover for t in tracks)
    capsys.readouterr()
    assert covers.main([str(d)]) == 0
    assert "1 already good" in capsys.readouterr().out


def test_smaller_folder_cover_wins(album):
    d, tracks = album
    (d / "folder.jpg").write_bytes(jpeg(1200, 1200))
    covers.main([str(d)])
    small = jpeg(300, 300)
    (d / "folder.jpg").write_bytes(small)
    assert covers.main([str(d)]) == 0
    assert all(tags.read_cover(str(t)) == small for t in tracks)


def test_not_an_image_refused(album, capsys):
    d, tracks = album
    (d / "folder.jpg").write_bytes(b"pas une image")
    assert covers.main([str(d)]) == 0
    assert "1 without folder.jpg" in capsys.readouterr().out
    assert all(tags.read_cover(str(t)) is None for t in tracks)


def test_bracket_folder_taken_literally(album):
    d, tracks = album
    assert list(covers.album_folders([str(d)])) == [(str(d), sorted(map(str, tracks)))]
