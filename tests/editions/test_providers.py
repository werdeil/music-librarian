"""Discogs and iTunes as edition providers, on recorded answers."""
# pylint: disable=redefined-outer-name  # pytest fixtures
import pytest
import requests

from music_librarian import editions
from music_librarian.editions import discogs, itunes


class Answer:
    def __init__(self, status, payload):
        self.status_code, self.payload = status, payload

    def json(self):
        return self.payload


@pytest.fixture
def web(monkeypatch):
    """The requests asked, each answered by the first of `answers` ({url part: payload}) that matches."""
    asked, answers = [], {}

    def get(url, params=None, headers=None, **_kwargs):
        asked.append((url, params, headers))
        for part, payload in answers.items():
            if part in url:
                return payload if isinstance(payload, Answer) else Answer(200, payload)
        return Answer(404, {})
    monkeypatch.setattr(requests, "get", get)
    for module in (discogs, itunes):
        monkeypatch.setattr(module, "INTERVAL", 0)
    return asked, answers


ALBUM = {"artist": "Arctic Monkeys", "title": "The Car", "mbid": "", "barcode": "", "tracks": 10}
ITUNES = {"collectionType": "Album", "collectionName": "The Car", "artistName": "Arctic Monkeys",
          "releaseDate": "2022-10-21T07:00:00Z", "country": "FRA", "copyright": "℗ 2022 Domino",
          "trackCount": 10, "collectionViewUrl": "https://music.apple.com/fr/album/1"}


def test_itunes(web, monkeypatch):
    asked, answers = web
    monkeypatch.setenv("ITUNES_COUNTRY", "FR")
    near = [{**ITUNES, "collectionName": "The Car (Deluxe Edition)", "collectionViewUrl": "deluxe"},
            {**ITUNES, "collectionName": "The Car - EP", "collectionViewUrl": "ep"},
            {**ITUNES, "collectionName": "AM", "collectionViewUrl": "other album"},
            {**ITUNES, "artistName": "The Cars", "collectionViewUrl": "other artist"},
            {**ITUNES, "collectionName": "Cars", "artistName": "Pixar", "collectionViewUrl": "film"}]
    answers["/search"] = {"results": [ITUNES, {"collectionType": "Compilation"}]}
    found = itunes.fetch(ALBUM)
    assert found == [{"title": "The Car", "artist": "Arctic Monkeys", "date": "2022-10-21", "country": "FRA",
                      "label": "℗ 2022 Domino", "catalog": "", "barcode": "", "tracks": 10,
                      "format": "Digital", "url": "https://music.apple.com/fr/album/1", "exact": False}]
    assert asked[0][1]["term"] == "Arctic Monkeys The Car" and asked[0][1]["country"] == "fr"
    answers["/search"] = {"results": [ITUNES, *near]}
    assert [e["url"] for e in itunes.fetch(ALBUM)] == ["https://music.apple.com/fr/album/1", "deluxe", "ep"]
    answers["/lookup"] = {"results": [ITUNES]}
    assert itunes.fetch({**ALBUM, "barcode": "123"})[0]["exact"]


def test_discogs(web, monkeypatch):
    asked, answers = web
    assert discogs.fetch(ALBUM) == [] and not asked                      # without a token
    monkeypatch.setenv("DISCOGS_TOKEN", "t0k")
    answers["database/search"] = {"results": [
        {"title": "Arctic Monkeys - The Car", "year": "2022", "country": "UK", "label": ["Domino", "Domino"],
         "catno": "WIGLP499", "format": ["Vinyl", "LP"], "barcode": ["887828"], "uri": "/release/1-The-Car"}]}
    [found] = discogs.fetch(ALBUM)
    assert (found["title"], found["artist"], found["date"], found["label"], found["catalog"],
            found["format"], found["url"]) == ("The Car", "Arctic Monkeys", "2022", "Domino", "WIGLP499",
                                               "Vinyl; LP", "https://www.discogs.com/release/1-The-Car")
    assert asked[0][2]["Authorization"] == "Discogs token=t0k"
    assert asked[0][1] == {"artist": "Arctic Monkeys", "release_title": "The Car", "type": "release",
                           "per_page": 50}


def test_providers_in_their_order(monkeypatch):
    monkeypatch.delenv("EDITION_PROVIDERS")
    assert [name for name, _ in editions.enabled_providers()] == \
        ["musicbrainz", "discogs", "itunes"]
    monkeypatch.setenv("DISCOGS_TOKEN", "t0k")
    assert [name for name, _ in editions.enabled_providers()] == \
        ["discogs", "musicbrainz", "itunes"]
    monkeypatch.setenv("EDITION_PROVIDERS", "itunes, deezer")
    assert [name for name, _ in editions.enabled_providers()] == ["itunes"]
