"""iTunes Search API: album genre lookup, never reaching the real service."""
# pylint: disable=redefined-outer-name,unused-argument  # pytest fixtures
import pytest
import requests

from music_librarian.integrations import itunes


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload, self.status_code = payload, status_code

    def json(self):
        return self.payload


def album(artist, title, genre):
    return {"artistName": artist, "collectionName": title, "primaryGenreName": genre}


@pytest.fixture
def answer(monkeypatch):
    calls = []

    def set_answer(results=(), status_code=200, error=None):
        def get(url, params=None, **kw):
            calls.append(params)
            if error:
                raise error
            return FakeResponse({"results": list(results)}, status_code)
        monkeypatch.setattr(itunes.requests, "get", get)
        return calls
    return set_answer


def client():
    return itunes.Client("fr", interval=0)


def test_genre_of_the_matching_album(answer):
    calls = answer([album("Massive Attack", "Heligoland", "Electronic"),
                    album("Massive Attack", "Mezzanine (Remastered)", "Alternative")])
    assert client().album_genre("Massive Attack", "Mezzanine") == "Alternative"
    assert calls[0]["country"] == "fr" and calls[0]["entity"] == "album"


def test_release_suffix_and_article_ignored(answer):
    answer([album("The Shoes", "Crack My Bones - EP", "Electronic")])
    assert client().album_genre("Shoes", "Crack My Bones") == "Electronic"


def test_other_artist_not_taken(answer):
    answer([album("Tribute Band", "Mezzanine", "Pop")])
    assert client().album_genre("Massive Attack", "Mezzanine") is None


@pytest.mark.parametrize("kw", [{"status_code": 403}, {"error": requests.ConnectionError("down")}])
def test_unavailable(answer, kw):
    answer(**kw)
    with pytest.raises(itunes.Unavailable):
        client().album_genre("Massive Attack", "Mezzanine")


def test_from_env(monkeypatch):
    monkeypatch.delenv("GENRE_LOOKUP", raising=False)
    assert itunes.from_env() is None
    monkeypatch.setenv("GENRE_LOOKUP", "iTunes")
    monkeypatch.setenv("ITUNES_COUNTRY", "FR")
    assert itunes.from_env().country == "fr"
