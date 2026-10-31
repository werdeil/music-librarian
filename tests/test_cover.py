"""cover: an image put as an album's folder.jpg, then embedded into its tracks (cover.py)."""
# pylint: disable=redefined-outer-name  # pytest fixtures
import struct

import pytest

from music_librarian import cli, cover
from music_librarian.core import runs
from music_librarian.core.lock import EXIT_STOPPED, STOP
from music_librarian.media import artwork, tags


def jpeg(width, height):
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 17, 8, height, width, 3)
    return b"\xff\xd8" + sof + b"\x01\x11\x00\x02\x11\x01\x03\x11\x01"


OLD, NEW = jpeg(300, 300), jpeg(1200, 1200)


@pytest.fixture
def lib(tmp_path, make_track):
    root = tmp_path / "Music"
    for n, ext in ((1, ".m4a"), (2, ".mp3")):
        make_track(root / f"Beyonce/4/0{n} T{n}{ext}", title=f"T{n}", artist="Beyonce", album="4", track=n)
    (root / "Beyonce/4/Folder.jpg").write_bytes(OLD)
    return root


@pytest.fixture
def run(lib, tmp_path):
    state = tmp_path / "state"
    image = tmp_path / "new.jpg"
    image.write_bytes(NEW)

    def run_cover(*args, folder="Beyonce/4", picture=image):
        return cli.main(["cover", "--root", str(lib), "--state-dir", str(state), folder, str(picture), *args])

    run_cover.state = state
    return run_cover


def covers_of(lib):
    return [tags.read_cover(p) for p in sorted((lib / "Beyonce/4").glob("*.m*"))]


def test_dry_run_changes_nothing(lib, run, capsys):
    assert run() == 0
    out = capsys.readouterr().out
    assert "COVER    folder.jpg  1200x1200, replacing 300x300  Beyonce/4" in out
    assert "WOULD    embed 1200x1200 into 2 of 2 tracks" in out and "to embed: 2" in out
    assert (lib / "Beyonce/4/Folder.jpg").read_bytes() == OLD and covers_of(lib) == [None, None]
    assert not (run.state / "journal").exists() and not (run.state / cover.KEPT).exists()


def test_applied_then_undone(lib, run, tmp_path, capsys):
    assert run("--apply") == 0
    assert [p.name for p in (lib / "Beyonce/4").glob("*.jpg")] == ["folder.jpg"]
    assert (lib / "Beyonce/4/folder.jpg").read_bytes() == NEW and covers_of(lib) == [NEW, NEW]
    assert (tmp_path / "new.jpg").exists()
    out = capsys.readouterr().out
    assert "embedded: 2" in out
    [journal] = (run.state / "journal").glob("*.jsonl")
    assert runs.recent(run.state, 1)[0]["command"] == "cover"

    assert cli.main(["undo", str(journal), "--root", str(lib), "--state-dir", str(run.state), "--apply"]) == 0
    assert [p.name for p in (lib / "Beyonce/4").glob("*.jpg")] == ["Folder.jpg"]
    assert (lib / "Beyonce/4/Folder.jpg").read_bytes() == OLD


def test_same_image_only_embedded(lib, run, capsys):
    (lib / "Beyonce/4/Folder.jpg").write_bytes(NEW)
    assert run("--apply") == 0
    assert "SKIPPED  (folder.jpg already holds this image)" in capsys.readouterr().out
    assert [p.name for p in (lib / "Beyonce/4").glob("*.jpg")] == ["Folder.jpg"]
    assert covers_of(lib) == [NEW, NEW]
    assert not (run.state / "journal").exists()


def test_album_without_a_cover(lib, run):
    (lib / "Beyonce/4/Folder.jpg").unlink()
    assert run("--apply") == 0
    assert (lib / "Beyonce/4/folder.jpg").read_bytes() == NEW


@pytest.mark.parametrize("folder", ["Beyonce", "../", "Beyonce/9"])
def test_not_an_album_refused(run, folder, capsys):
    assert run("--apply", folder=folder) == 2
    assert "REFUSED  (not an album folder of the library)" in capsys.readouterr().out


def test_not_an_image_refused(lib, run, tmp_path, capsys):
    png = tmp_path / "cover.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    assert run("--apply", picture=png) == 2
    assert "REFUSED  (not an image: " in capsys.readouterr().out
    assert (lib / "Beyonce/4/Folder.jpg").read_bytes() == OLD


@pytest.mark.parametrize("name", ["cover.png", "cover.webp", "cover.bmp"])
def test_any_format_converted_to_jpeg(lib, run, image, tmp_path, name, capsys):
    path = tmp_path / name
    path.write_bytes(image(name, 64, 48))
    assert run("--apply", picture=path) == 0
    assert f"CONVERT  {name} to JPEG" in capsys.readouterr().out
    written = (lib / "Beyonce/4/folder.jpg").read_bytes()
    assert artwork.image_size(written) == ("jpeg", 64, 48) and covers_of(lib) == [written, written]


def test_stop_leaves_the_cover(lib, run, monkeypatch):
    monkeypatch.setattr(STOP, "is_set", lambda: True)
    assert run("--apply") == EXIT_STOPPED
    assert (lib / "Beyonce/4/Folder.jpg").read_bytes() == OLD and covers_of(lib) == [None, None]
