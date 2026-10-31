"""Lyrics on the dashboard: each track's state on its album's page, a lookup of the album, the
tracks without lyrics."""
# pylint: disable=redefined-outer-name  # pytest fixtures
import html
import re
import sys

import pytest

from music_librarian import enrich, scan
from music_librarian.core import runs
from music_librarian.core.log import Log
from music_librarian.lyrics.misses import Misses
from music_librarian.media import fields, tags

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
    flat = page.replace("\n", "").replace("  ", "")
    assert '>yes· <a href="/library/track/lyrics?folder=Beyonce/4&amp;file=01+T1.m4a">edit</a></td>' in flat
    assert "not found on " in page and "—" in page and ">add</a>" in page
    assert "Look up the album&#39;s lyrics" in page


def test_lookup_queued_then_applied(d, lib, tool):
    response = d.client.post("/library/album/lyrics", data={"csrf": d.token, "folder": "Beyonce/4"})
    assert response.status_code == 302
    d.requests.tick(Log())
    [dry] = runs.recent(d.state, 1)
    assert (dry["command"], dry["apply"], dry["options"]) == ("lyrics", False, {"folder": "Beyonce/4"})
    assert tool() == [f"--retry --look-up --dry-run {lib / 'Beyonce/4'}"]
    assert "NONE     x" in runs.log_path(d.state, dry["id"]).read_text(encoding="utf-8")
    d.client.post(f"/runs/{dry['id']}/apply", data={"csrf": d.token})
    d.requests.tick(Log())
    assert tool()[1] == f"--retry --look-up {lib / 'Beyonce/4'}"


@pytest.mark.parametrize("folder", ["../", "Beyonce/9"])
def test_lookup_of_a_folder_outside_the_library(d, folder):
    response = d.client.post("/library/album/lyrics", data={"csrf": d.token, "folder": folder})
    assert response.status_code == 404 and d.requests.pending() == []


def test_tracks_without_lyrics(d):
    library_page = d.client.get("/library", headers=EN).get_data(as_text=True)
    assert 'Tracks without lyrics: 2 <a href="/library/lyrics">see</a>' in library_page
    page = d.client.get("/library/lyrics", headers=EN).get_data(as_text=True)
    assert "/library/album?folder=Beyonce/4" in page and "<td>2 / 3</td>" in page
    assert "<td>—</td>" not in page                         # one of them was looked up in vain
    home = d.client.get("/", headers=EN).get_data(as_text=True)
    tile = home[home.index("Tracks without lyrics") - 300:home.index("Tracks without lyrics")]
    assert 'class="tile tile-info"' in tile and '<span class="tile-value">2' in tile


def lyrics_page(d, file):
    response = d.client.get(f"/library/track/lyrics?folder=Beyonce/4&file={file}", headers=EN)
    return response.get_data(as_text=True)


def lyrics_form(d, file, **changes):
    """The lyrics page's form as a browser would post it."""
    page = lyrics_page(d, file)
    form = {"csrf": d.token, "folder": "Beyonce/4", "file": file,
            "seen": html.unescape(re.search(r'name="seen" value="([^"]*)"', page)[1]),
            "lyrics": html.unescape(re.search(r'<textarea[^>]*>([^<]*)</textarea>', page)[1])}
    form.update(changes)
    return form


def test_lyrics_page(d):
    page = lyrics_page(d, "01 T1.m4a")
    assert ">la la</textarea>" in page and "Delete the lyrics" in page and "← 4</a>" in page
    page = lyrics_page(d, "02 T2.m4a")
    assert "></textarea>" in page and "Delete the lyrics" not in page
    for query in ("folder=Beyonce/4&file=../4/01 T1.m4a", "folder=../&file=x", "folder=Beyonce/4&file=9.m4a"):
        assert d.client.get(f"/library/track/lyrics?{query}", headers=EN).status_code == 404


def test_untouched_lyrics_ask_nothing(d):
    form = lyrics_form(d, "01 T1.m4a", lyrics="la la\r\n\r\n")
    assert d.client.post("/library/track/lyrics", data=form).location.endswith("unchanged=1")
    assert d.requests.pending() == []


def test_lyrics_edited_then_undone(d, lib):
    track = lib / "Beyonce/4/01 T1.m4a"
    d.client.post("/library/track/lyrics", data=lyrics_form(d, "01 T1.m4a", lyrics="la la\r\nlo lo  \r\n"))
    [asked] = d.requests.pending()
    assert (asked.command, asked.options) == ("tag", {"edits": [["Beyonce/4/01 T1.m4a", "lyrics",
                                                                 "la la\r\nlo lo  \r\n", ["la la"]]]})
    d.requests.tick(Log())
    [dry] = runs.recent(d.state, 1)
    page = d.client.get(f"/runs/{dry['id']}", headers=EN).get_data(as_text=True)
    assert "TAG      lyrics: &#34;1 line&#34; → &#34;2 lines&#34;  Beyonce/4/01 T1.m4a" in page
    assert tags.read_lyrics(track) == "la la"
    d.client.post(f"/runs/{dry['id']}/apply", data={"csrf": d.token})
    d.requests.tick(Log())
    assert tags.read_lyrics(track) == "la la\nlo lo"
    [applied] = runs.recent(d.state, 1)
    journal = re.search(r"journal: .*/(\S+\.jsonl)", runs.log_path(d.state, applied["id"]).read_text())[1]
    d.client.post("/passes", data={"csrf": d.token, "command": "undo", "journal": journal, "apply": "1"})
    d.requests.tick(Log())
    assert tags.read_lyrics(track) == "la la"


def test_lyrics_deleted_are_not_looked_up_again(d, lib):
    track = lib / "Beyonce/4/01 T1.m4a"
    d.client.post("/library/track/lyrics", data=lyrics_form(d, "01 T1.m4a", delete="1"))
    [asked] = d.requests.pending()
    assert asked.options["edits"] == [["Beyonce/4/01 T1.m4a", "lyrics", "", ["la la"]]]
    d.requests.tick(Log())
    [dry] = runs.recent(d.state, 1)
    assert tags.read_lyrics(track) == "la la"
    d.client.post(f"/runs/{dry['id']}/apply", data={"csrf": d.token})
    d.requests.tick(Log())
    assert fields.lyrics(track) == []
    with Misses(d.state) as misses:
        assert misses.recent(track, {"artist": "Beyonce", "title": "T1", "album": "4"})
    page = d.client.get("/library/album?folder=Beyonce/4", headers=EN).get_data(as_text=True)
    assert page.count("not found on ") == 2


def test_lyrics_changed_meanwhile_refused(d, lib, capsys):
    form = lyrics_form(d, "01 T1.m4a", lyrics="new")
    tags.write_lyrics(lib / "Beyonce/4/01 T1.m4a", "other")
    d.client.post("/library/track/lyrics", data=form)
    d.requests.submit("tag", {"edits": d.requests.pending()[0].options["edits"]}, True)
    d.requests.tick(Log())
    assert tags.read_lyrics(lib / "Beyonce/4/01 T1.m4a") == "other"
    assert 'REFUSED  (lyrics changed since: "1 line")' in capsys.readouterr().out
