"""scan: the library's metadata read into the cache, then only what changed."""
# pylint: disable=redefined-outer-name,unused-argument  # pytest fixtures
import pytest

from music_librarian import cli
from music_librarian.core.lock import EXIT_STOPPED, STOP
from music_librarian.media import tags


@pytest.fixture
def lib(tmp_path, make_track):
    root = tmp_path / "Musique"
    tracks = [make_track(root / "A" / "B" / f"0{i}.m4a", title=f"T{i}") for i in (1, 2)]
    tags.write_genre(str(tracks[0]), "Rock")
    make_track(root / "C" / "D" / "01.mp3", title="T3")
    return root, tracks


def run(root, tmp_path):
    return cli.main(["scan", "--root", str(root), "--state-dir", str(tmp_path / "state")])


def test_second_scan_from_cache(lib, tmp_path, capsys):
    root, tracks = lib
    assert run(root, tmp_path) == 0
    out = capsys.readouterr().out
    assert "files: 3" in out and "read: 3" in out and "from cache:" not in out
    assert "       1  Rock" in out and "       2  (none)" in out
    tags.write_genre(str(tracks[1]), "Rock")
    assert run(root, tmp_path) == 0
    out = capsys.readouterr().out
    assert "read: 1" in out and "from cache: 2" in out and "       2  Rock" in out


def test_force_reads_everything(lib, tmp_path, capsys):
    root, _ = lib
    run(root, tmp_path)
    assert cli.main(["scan", "--root", str(root), "--state-dir", str(tmp_path / "state"), "--force"]) == 0
    out = capsys.readouterr().out
    forced = out.split("[SCAN --force]")[1]
    assert "read: 3" in forced and "from cache:" not in forced


def test_deleted_files_removed(lib, tmp_path, capsys):
    root, tracks = lib
    run(root, tmp_path)
    tracks[1].unlink()
    assert run(root, tmp_path) == 0
    out = capsys.readouterr().out
    assert "files: 2" in out and "removed: 1" in out


def test_stop_keeps_the_cache(lib, tmp_path, capsys):
    root, _ = lib
    run(root, tmp_path)
    STOP.set()
    assert run(root, tmp_path) == EXIT_STOPPED
    assert "removed:" not in capsys.readouterr().out
