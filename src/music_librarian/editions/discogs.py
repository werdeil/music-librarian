"""Discogs: the releases its users catalogue, the most precise on editions (country, label, catalog
number, format). It needs a personal access token, $DISCOGS_TOKEN: without one, it is not asked."""
import os

from music_librarian.editions.common import edition as an_edition, get_json

SEARCH = "https://api.discogs.com/database/search"
SITE = "https://www.discogs.com"
INTERVAL = 1.0   # Discogs allows 60 requests a minute with a token
LAST = [0.0]   # when Discogs was last asked, by any module


def token():
    return os.environ.get("DISCOGS_TOKEN", "").strip()


def edition(result, exact=False):
    """The edition a Discogs search result stands for, as editions.look_up() lists them; its date is
    a year at most."""
    artist, _, title = (result.get("title") or "").partition(" - ")
    return an_edition(SITE + (result.get("uri") or ""), exact, title=title or artist,
                      artist=artist if title else "", date=result.get("year"), country=result.get("country"),
                      label="; ".join(dict.fromkeys(result.get("label") or [])), catalog=result.get("catno"),
                      barcode=next(iter(result.get("barcode") or []), ""),
                      format="; ".join(dict.fromkeys(result.get("format") or [])))


def _search(params):
    headers = {"Authorization": f"Discogs token={token()}"}
    found = get_json(SEARCH, {**params, "type": "release", "per_page": 50}, LAST, INTERVAL, headers)
    return (found or {}).get("results") or []


def fetch(album):
    """The editions of `album` ({"artist", "title", "barcode"}): those its barcode names, else those of
    its artist and title; none without a token. Raises ProviderUnavailable."""
    if not token():
        return []
    if album.get("barcode"):
        found = _search({"barcode": album["barcode"]})
        if found:
            return [edition(r, exact=True) for r in found]
    if not album.get("title") or not album.get("artist"):
        return []
    return [edition(r) for r in _search({"artist": album["artist"], "release_title": album["title"]})]
