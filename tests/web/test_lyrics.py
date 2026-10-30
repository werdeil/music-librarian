"""Lyrics on the dashboard: each track's state on its album's page, a lookup of the album, the
albums looked up in vain."""
# pylint: disable=redefined-outer-name  # pytest fixtures
import sys

import pytest

from music_librarian import enrich, scan
from music_librarian.core import runs
from music_librarian.core.log import Log
from music_librarian.lyrics.misses import Misses
from music_librarian.media import tags

EN = {"Accept-Language": "en"}


@pytest.fixture
def lib(tmp_path, make_track):
    root = tmp_path / "Music"
    for n in (1, 2, 3):
        make_track(root / f"Beyonce/4/0{n} T{n}.m4a", title=f"T{n}", artist="Beyonce",
                   albumartist="Beyonce", album="4", track=n)
    tags.write_lyrics(root / "Beyonce/4/01 T1.m4a", "la la")
    with Misses(tmp_path / "state") as misses:
        misses.record(root / "Beyonce/4/02 T2.m4a", {"artist": "Beyonce", "title": "T2", "album": "4"})
    scan.run(root, tmp_path / "state", Log())
    return root


@pytest.fixture
def d(lib, tmp_path, serve):
    return serve(lib, tmp_path / "state", tmp_path / "Inbox")


@pytest.fixture
def tool(tmp_path, monkeypatch):
    """A lyrics tool that records its arguments instead of looking anything up."""
    calls = tmp_path / "calls.log"
    script = (f"import sys; open({str(calls)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n'); "
              "print('NONE     x')")
    monkeypatch.setattr(enrich, "enrichers", lambda: [("lyrics", [sys.executable, "-c", script])])
    return lambda: calls.read_text().splitlines() if calls.exists() else []


def test_album_page_shows_each_track_s_lyrics(d):
    page = d.client.get("/library/album?folder=Beyonce/4", headers=EN).get_data(as_text=True)
    assert ">yes</td>" in page.replace("\n", "").replace("  ", "")
    assert "not found on " in page and "—" in page
    assert "Look up the album&#39;s lyrics" in page


def test_lookup_queued_then_applied(d, lib, tool):
    response = d.client.post("/library/album/lyrics", data={"csrf": d.token, "folder": "Beyonce/4"})
    assert response.status_code == 302
    d.requests.tick(Log())
    [dry] = runs.recent(d.state, 1)
    assert (dry["command"], dry["apply"], dry["options"]) == ("lyrics", False, {"folder": "Beyonce/4"})
    assert tool() == [f"--retry --dry-run {lib / 'Beyonce/4'}"]
    assert "NONE     x" in runs.log_path(d.state, dry["id"]).read_text(encoding="utf-8")
    d.client.post(f"/runs/{dry['id']}/apply", data={"csrf": d.token})
    d.requests.tick(Log())
    assert tool()[1] == f"--retry {lib / 'Beyonce/4'}"


@pytest.mark.parametrize("folder", ["../", "Beyonce/9"])
def test_lookup_of_a_folder_outside_the_library(d, folder):
    response = d.client.post("/library/album/lyrics", data={"csrf": d.token, "folder": folder})
    assert response.status_code == 404 and d.requests.pending() == []


def test_albums_looked_up_in_vain(d):
    page = d.client.get("/library", headers=EN).get_data(as_text=True)
    assert "lyrics not found: 1 album(s)" in page
    page = d.client.get("/library/lyrics", headers=EN).get_data(as_text=True)
    assert "/library/album?folder=Beyonce/4" in page and "<td>1</td>" in page
