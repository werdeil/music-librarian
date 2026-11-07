"""Lyrics on the dashboard: each track's lyrics on its album's page, edited and previewed with the
album's form, looked up online one track at a time; the tracks without lyrics."""
# pylint: disable=redefined-outer-name  # pytest fixtures
import html
import re

import pytest

from music_librarian import scan
from music_librarian.enrich import lyrics as enrich_lyrics
from music_librarian.core.log import Log
from music_librarian.lyrics import misses
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


def album_form(d, **changes):
    """The album page's form as a browser would post it, the lyrics boxes included."""
    page = d.client.get("/library/album?folder=Beyonce/4", headers=EN).get_data(as_text=True)
    form = {"csrf": d.token, "folder": "Beyonce/4"}
    for tag_, name, value in re.findall(r'(<input[^>]* name="([^"]+)" value="([^"]*)"[^>]*>)', page):
        if 'type="checkbox"' not in tag_ or " checked" in tag_:
            form[name] = html.unescape(value)
    for name, value in re.findall(r'<textarea name="([^"]+)"[^>]*>([^<]*)</textarea>', page):
        form[name] = html.unescape(value)
    form["seen"] = html.unescape(re.search(r'name="seen" value="([^"]*)"', page)[1])
    form.update(changes)
    return form


def preview(d, **changes):
    return d.client.post("/library/album", data=album_form(d, **changes), headers=EN).get_data(as_text=True)


def apply(d, **changes):
    """Apply the form's changes, then return the album's page following the pass."""
    following = d.client.post("/library/album", data=album_form(d, apply="1", **changes)).location
    d.requests.tick(Log())
    return d.client.get(following, headers=EN).get_data(as_text=True)


def test_album_page_shows_each_track_s_lyrics(d):
    page = d.client.get("/library/album?folder=Beyonce/4", headers=EN).get_data(as_text=True)
    assert 'data-lyrics-toggle="lyrics-0"' in page and 'title="See and edit the lyrics">yes</button>' in page
    box = '<textarea name="t{}.lyrics" class="lyrics" rows="8" aria-label="Lyrics · {}">{}</textarea>'
    assert box.format(0, "01 T1.m4a", "la la") in page and box.format(1, "02 T2.m4a", "") in page
    assert page.count("Looked up in vain on ") == 1 and page.count(">Look up online</button>") == 3
    assert "data-open" not in page and "Look up the album" not in page


def test_untouched_lyrics_ask_nothing(d):
    response = d.client.post("/library/album", data=album_form(d, **{"t0.lyrics": "la la\r\n\r\n"}))
    assert response.location.endswith("unchanged=1") and d.requests.pending() == []


def test_lyrics_previewed_applied_then_undone(d, lib):
    track = lib / "Beyonce/4/01 T1.m4a"
    page = preview(d, **{"t0.lyrics": "la la\r\nlo lo  \r\n"})
    assert "<del>1 line</del> → <ins>2 lines</ins>" in page and "· 01 T1.m4a" in page
    assert 'id="lyrics-0" data-lyrics-row data-open' in page and 'id="lyrics-1" data-lyrics-row>' in page
    assert tags.read_lyrics(track) == "la la"
    page = apply(d, **{"t0.lyrics": "la la\r\nlo lo  \r\n"})
    assert tags.read_lyrics(track) == "la la\nlo lo"
    assert "<h2>Applied</h2>" in page and ">la la\nlo lo</textarea>" in page
    journal = re.search(r'name="journal" value="([^"]+)"', page)[1]
    d.client.post("/passes", data={"csrf": d.token, "command": "undo", "journal": journal, "apply": "1"})
    d.requests.tick(Log())
    assert tags.read_lyrics(track) == "la la"


def test_lyrics_emptied_are_removed_and_looked_up_again(d, lib):
    track, meta = lib / "Beyonce/4/01 T1.m4a", {"artist": "Beyonce", "title": "T1", "album": "4"}
    with Misses(d.state) as misses:
        misses.record(track, meta)
    assert "<del>1 line</del> → <ins>(none)</ins>" in preview(d, **{"t0.lyrics": ""})
    apply(d, **{"t0.lyrics": ""})
    assert fields.lyrics(track) == []
    with Misses(d.state) as misses:
        assert not misses.recent(track, meta)


def test_lyrics_changed_meanwhile_refused(d, lib):
    form = album_form(d, **{"t0.lyrics": "new"})
    tags.write_lyrics(lib / "Beyonce/4/01 T1.m4a", "other")
    page = d.client.post("/library/album", data=form, headers=EN).get_data(as_text=True)
    assert 'REFUSED  (lyrics changed since: &#34;1 line&#34;)' in page
    assert tags.read_lyrics(lib / "Beyonce/4/01 T1.m4a") == "other"


def test_track_marked_instrumental(d, lib):
    page = d.client.get("/library/album?folder=Beyonce/4", headers=EN).get_data(as_text=True)
    assert 'name="t1.instrumental" value="1" data-instrumental="[Instrumental]" data-target="t1.lyrics">' \
        in page
    assert "<del>(none)</del> → <ins>instrumental</ins>" in preview(d, **{"t1.instrumental": "1"})
    page = d.client.post("/library/album", data=album_form(d, **{"t0.lyrics": "a\nb"}),
                         headers={"Accept-Language": "fr"}).get_data(as_text=True)
    assert "<del>1 ligne</del> → <ins>2 lignes</ins>" in page
    apply(d, **{"t1.instrumental": "1", "t1.lyrics": "typed"})
    assert tags.read_lyrics(lib / "Beyonce/4/02 T2.m4a") == "[Instrumental]"
    page = d.client.get("/library/album?folder=Beyonce/4", headers=EN).get_data(as_text=True)
    assert 'data-target="t1.lyrics" checked>' in page
    assert 'title="See and edit the lyrics">instrumental</button>' in page
    response = d.client.post("/library/album", data=album_form(d, **{"t1.instrumental": "1"}))
    assert response.location.endswith("unchanged=1")


@pytest.fixture
def providers(monkeypatch):
    """The providers' answer to the lookups, set by the test; the tracks asked for are kept."""
    asked, answer = [], {}
    monkeypatch.setattr(enrich_lyrics, "fetch_lyrics", lambda **kw: asked.append(kw) or dict(answer))
    return asked, answer


def look_up(d, file="02 T2.m4a"):
    return d.client.post("/library/track/lyrics/lookup", headers=EN,
                         data={"csrf": d.token, "folder": "Beyonce/4", "file": file})


FOUND = "Found on {}: read them over, then preview."


@pytest.mark.parametrize("answer, text, message", [
    ({"lyrics": "found\ntext", "source": "lrclib"}, "found\ntext", FOUND.format("LRCLIB")),
    ({"synced": "[00:01.00] sung", "source": "netease"}, "sung", FOUND.format("NetEase")),
    ({"instrumental": True, "source": "lrclib"}, "[Instrumental]", "Instrumental according to LRCLIB."),
    ({"source": "none"}, None, "Nothing found."),
    ({"source": "rejected"}, None, "Found, but not for this track: the title, artist or duration differ."),
    ({"source": "unavailable"}, None, "The providers do not answer: try again later."),
])
def test_lookup_of_one_track_writes_nothing(d, lib, providers, answer, text, message):
    asked, providers_answer = providers
    providers_answer.update(answer)
    assert look_up(d).get_json() == {"text": text, "message": message}
    assert [(a["artist"], a["title"], a["album"]) for a in asked] == [("Beyonce", "T2", "4")]
    assert fields.lyrics(lib / "Beyonce/4/02 T2.m4a") == [] and d.requests.pending() == []


def test_lookup_in_vain_recorded(d, lib, providers):
    track = lib / "Beyonce/4/03 T3.m4a"
    providers[1].update(source="unavailable")
    look_up(d, "03 T3.m4a")
    assert track not in misses_recorded(d)
    providers[1].update(source="none")
    look_up(d, "03 T3.m4a")
    assert track in misses_recorded(d)


def misses_recorded(d):
    return misses.recorded(d.state)


@pytest.mark.parametrize("folder, file", [("Beyonce/4", "../4/01 T1.m4a"), ("../", "x"),
                                          ("Beyonce/4", "9.m4a")])
def test_lookup_of_a_track_outside_the_library(d, providers, folder, file):
    response = d.client.post("/library/track/lyrics/lookup",
                             data={"csrf": d.token, "folder": folder, "file": file})
    assert response.status_code == 404 and providers[0] == []


def test_tracks_without_lyrics(d):
    library_page = d.client.get("/library", headers=EN).get_data(as_text=True)
    assert 'href="/library?only=lyrics">Without lyrics <span class="muted">1</span></a>' in library_page
    page = d.client.get("/library/lyrics", headers=EN).get_data(as_text=True)
    assert "/library/album?folder=Beyonce/4" in page and "<td>2 / 3</td>" in page
    assert "<td>—</td>" not in page                         # one of them was looked up in vain
    home = d.client.get("/", headers=EN).get_data(as_text=True)
    tile = home[home.index("Tracks without lyrics") - 300:home.index("Tracks without lyrics")]
    assert 'class="tile-line tile-info"' in tile and '<span class="tile-value">2' in tile
