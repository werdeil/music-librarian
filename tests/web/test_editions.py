"""An album's editions looked up online from its page, by hand only."""
# pylint: disable=redefined-outer-name  # pytest fixtures
import pytest

from music_librarian import editions, scan
from music_librarian.editions.common import ProviderUnavailable
from music_librarian.core.log import Log
from music_librarian.media import fields

EN = {"Accept-Language": "en"}


@pytest.fixture
def d(tmp_path, make_track, serve):
    root = tmp_path / "Music"
    for n in (1, 2):
        p = make_track(root / f"A/B/0{n} T{n}.m4a", title=f"T{n}", artist="A", albumartist="A", album="B",
                       track=n)
        fields.write(str(p), "year", ["1973"])
    scan.run(root, tmp_path / "state", Log())
    return serve(root, tmp_path / "state", tmp_path / "Inbox")


@pytest.fixture
def asked(monkeypatch):
    albums = []

    def provider(album):
        albums.append(album)
        return [{"title": "B", "artist": "A", "date": "2011-05-02", "country": "FR", "label": "L",
                 "catalog": "C1", "barcode": "", "tracks": 2, "format": "CD", "url": "https://mb/r1",
                 "exact": False},
                {"title": "B", "artist": "A", "date": "1973", "country": "GB", "label": "", "catalog": "",
                 "barcode": "", "tracks": 9, "format": "", "url": "https://mb/r2", "exact": False}]
    monkeypatch.setitem(editions.PROVIDERS, "musicbrainz", provider)
    return albums


def look_up(d, **typed):
    return d.client.post("/library/album/editions", headers=EN,
                         data={"csrf": d.token, "folder": "A/B", **typed}).get_data(as_text=True)


def test_button_on_the_album_page(d):
    page = d.client.get("/library/album?folder=A/B", headers=EN).get_data(as_text=True)
    assert 'data-editions="/library/album/editions"' in page and "Look the edition up</button>" in page


def test_editions_listed_and_nothing_written(d, asked):
    found = look_up(d, albumartist="", album="B typed")
    assert asked == [{"artist": "A", "title": "B typed", "mbid": "", "barcode": "", "tracks": 2}]
    assert "2 editions on MusicBrainz" in found
    assert '<tr class="edition-match">' in found and found.index("2011-05-02") < found.index("GB")
    assert '<button type="button" data-pick-year="2011">Take 2011</button>' in found
    assert '<a href="https://mb/r1" target="_blank" rel="noopener">B</a>' in found
    assert fields.read(str(d.ctx.root / "A/B/01 T1.m4a"))["year"] == ["1973"]


def test_nothing_found(d, monkeypatch):
    monkeypatch.setitem(editions.PROVIDERS, "musicbrainz", lambda album: [])
    assert "Nothing found." in look_up(d)


def test_nothing_found_but_a_provider_did_not_answer(d, monkeypatch):
    def down(_album):
        raise ProviderUnavailable("down")
    monkeypatch.setenv("EDITION_PROVIDERS", "musicbrainz,itunes")
    monkeypatch.setitem(editions.PROVIDERS, "musicbrainz", down)
    monkeypatch.setitem(editions.PROVIDERS, "itunes", lambda album: [])
    assert "Nothing found; no answer from MusicBrainz, try again later." in look_up(d)
    monkeypatch.setitem(editions.PROVIDERS, "itunes", down)
    assert "The providers do not answer: try again later." in look_up(d)


def test_outside_the_library(d, asked):
    response = d.client.post("/library/album/editions", data={"csrf": d.token, "folder": "../"})
    assert response.status_code == 404 and not asked
