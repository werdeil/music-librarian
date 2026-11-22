"""The editions of an album looked up online, ranked for its page."""
import pytest

from music_librarian import editions
from music_librarian.editions.common import ProviderUnavailable


def edition(title="B", date="", tracks=2, exact=False):
    return {"title": title, "artist": "A", "date": date, "country": "", "label": "", "catalog": "",
            "barcode": "", "tracks": tracks, "format": "", "url": "u", "exact": exact}


def test_album_of():
    snaps = [{"tags": {"ALBUM ARTIST": ["A"], "album": ["B"], "barcode": [" 1234 "]}},
             None, {"tags": {"musicbrainz_albumid": ["r1"]}}]
    assert editions.album_of(snaps) == {"artist": "A", "title": "B", "mbid": "r1", "barcode": "1234",
                                        "tracks": 3}
    assert editions.album_of(snaps, {"artist": " C ", "title": ""})["artist"] == "C"


def test_ranked():
    album = {"title": "B", "tracks": 2}
    found = editions.ranked([edition(date="1990"), edition(date="2011-05-02", exact=True),
                             edition(title="Other", date="1980"), edition(tracks=3, date="1970"),
                             edition(date="")], album)
    assert [(e["year"], e["matches"]) for e in found] == [("2011", True), ("1990", True), ("", True),
                                                         ("1980", True), ("1970", False)]


DOWN = ProviderUnavailable("down")


@pytest.mark.parametrize("brainz, store, expected", [
    ([edition(date="2011")], None, (["2011"], "musicbrainz", [])),
    ([], [], ([], "none", [])),
    (DOWN, [], ([], "partly unavailable", ["musicbrainz"])),
    (DOWN, DOWN, ([], "unavailable", ["musicbrainz", "itunes"])),
    (DOWN, [edition(date="2011")], (["2011"], "itunes", ["musicbrainz"])),
])
def test_look_up(monkeypatch, brainz, store, expected):
    def provider(answer):
        def ask(_album):
            if isinstance(answer, Exception):
                raise answer
            return answer
        return ask
    monkeypatch.setenv("EDITION_PROVIDERS", "musicbrainz,itunes")
    monkeypatch.setitem(editions.PROVIDERS, "musicbrainz", provider(brainz))
    monkeypatch.setitem(editions.PROVIDERS, "itunes", provider(store))
    found, source, unreachable = editions.look_up({"artist": "A", "title": "B", "tracks": 1})
    assert ([e["year"] for e in found], source, unreachable) == expected


def test_nothing_to_look_up_with(monkeypatch):
    monkeypatch.setenv("EDITION_PROVIDERS", "nobody")
    assert editions.look_up({"artist": "A", "title": "", "tracks": 1}) == ([], "no metadata", [])
    assert editions.look_up({"artist": "A", "title": "B", "tracks": 1}) == ([], "none", [])
