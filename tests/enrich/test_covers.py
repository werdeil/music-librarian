"""enrich.covers: folder.jpg embedded into the tags, on real files."""
# pylint: disable=redefined-outer-name,unused-argument  # pytest fixtures
import struct

import pytest

from music_librarian.core.lock import EXIT_STOPPED, STOP
from music_librarian.enrich import common, covers
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
    assert "WOULD    " in capsys.readouterr().out
    assert all(tags.read_cover(str(t)) is None for t in tracks)


def test_written_then_up_to_date(album, capsys):
    d, tracks = album
    cover = jpeg(600, 600)
    (d / "Folder.JPG").write_bytes(cover)              # case-insensitive
    assert covers.main([str(d)]) == 0
    assert all(tags.read_cover(str(t)) == cover for t in tracks)
    capsys.readouterr()
    assert covers.main([str(d)]) == 0
    assert "up to date: 1" in capsys.readouterr().out


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
    assert "without folder.jpg: 1" in capsys.readouterr().out
    assert all(tags.read_cover(str(t)) is None for t in tracks)


def test_bracket_folder_taken_literally(album):
    d, tracks = album
    assert list(common.album_folders([str(d)])) == [(str(d), sorted(map(str, tracks)))]


def test_stop_after_current_album(tmp_path, make_track, monkeypatch):
    albums = [tmp_path / name for name in ("A", "B")]
    for d in albums:
        make_track(d / "01 Titre.m4a", title="Titre")
        (d / "folder.jpg").write_bytes(jpeg(600, 600))
    real = covers.process

    def process_then_stop(*a):
        STOP.set()
        return real(*a)

    monkeypatch.setattr(covers, "process", process_then_stop)
    assert covers.main([str(tmp_path / "*")]) == EXIT_STOPPED
    assert [tags.read_cover(str(d / "01 Titre.m4a")) is not None for d in albums] == [True, False]


def test_write_failure_is_exit_2(album, monkeypatch):
    d, _ = album
    (d / "folder.jpg").write_bytes(jpeg(600, 600))

    def broken(path, data):
        raise tags.CoverTagError("read-only file")

    monkeypatch.setattr(tags, "write_cover", broken)
    assert covers.main([str(d)]) == 2
