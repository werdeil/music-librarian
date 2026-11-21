"""MusicBrainz as an edition provider: the release an MBID or a barcode names, else a search."""
# pylint: disable=redefined-outer-name  # pytest fixtures
import pytest
import requests

from music_librarian.editions import musicbrainz
from music_librarian.editions.common import ProviderUnavailable

RELEASE = {
    "id": "r1", "title": "Will of the People", "date": "2022-08-26", "country": "XE", "barcode": "1234",
    "artist-credit": [{"name": "Muse", "joinphrase": ""}], "track-count": 10,
    "label-info": [{"catalog-number": "WMG1", "label": {"name": "Warner"}},
                   {"catalog-number": None, "label": {"name": "Warner"}}],
    "media": [{"format": "CD", "track-count": 10}],
}


class Answer:
    def __init__(self, status, payload):
        self.status_code, self.payload = status, payload

    def json(self):
        return self.payload


@pytest.fixture
def mb(monkeypatch):
    """The requests MusicBrainz is asked, answered from `answers` in turn."""
    asked, answers = [], []

    def get(url, params=None, headers=None, **_kwargs):
        asked.append((url, params, headers))
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(musicbrainz, "INTERVAL", 0)
    return asked, answers


def test_edition():
    assert musicbrainz.edition(RELEASE, exact=True) == {
        "title": "Will of the People", "artist": "Muse", "date": "2022-08-26", "country": "XE",
        "label": "Warner", "catalog": "WMG1", "barcode": "1234", "tracks": 10, "format": "CD",
        "url": "https://musicbrainz.org/release/r1", "exact": True}


def test_mbid_first(mb):
    asked, answers = mb
    answers.append(Answer(200, RELEASE))
    found = musicbrainz.fetch({"artist": "Muse", "title": "W", "mbid": "r1", "barcode": "1234"})
    assert [e["url"] for e in found] == ["https://musicbrainz.org/release/r1"] and found[0]["exact"]
    assert asked[0][0] == "https://musicbrainz.org/ws/2/release/r1"
    assert "music-librarian" in asked[0][2]["User-Agent"]


def test_unknown_mbid_then_barcode_then_search(mb):
    asked, answers = mb
    answers += [Answer(404, {}), Answer(200, {"releases": []}), Answer(200, {"releases": [RELEASE]})]
    found = musicbrainz.fetch({"artist": 'Mu"se', "title": "Will", "mbid": "x", "barcode": "1234"})
    assert len(found) == 1 and not found[0]["exact"]
    assert asked[1][1]["query"] == 'barcode:"1234"'
    assert asked[2][1]["query"] == 'release:"Will" AND artist:"Mu\\"se"'


def test_unreachable(mb):
    _, answers = mb
    answers.append(requests.ConnectionError("down"))
    with pytest.raises(ProviderUnavailable):
        musicbrainz.fetch({"artist": "Muse", "title": "Will"})
    answers.append(Answer(503, {}))
    with pytest.raises(ProviderUnavailable):
        musicbrainz.fetch({"artist": "Muse", "title": "Will"})
