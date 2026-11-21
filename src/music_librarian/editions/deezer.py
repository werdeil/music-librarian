"""Deezer: the albums it streams, each an edition with its release date and label, often the date of
its digital release."""
from music_librarian.editions.common import ProviderUnavailable, edition as an_edition, get_json

API = "https://api.deezer.com"
INTERVAL = 0.1   # Deezer allows 50 requests every 5 seconds
DETAILED = 10    # the search gives neither date nor label: each album found is asked for them
NO_DATA = 800
_last = [0.0]


def _get(path, params=None):
    """Deezer's JSON for `path`, None for what it does not know. Raises ProviderUnavailable."""
    answer = get_json(f"{API}/{path}", params or {}, _last, INTERVAL)
    error = (answer or {}).get("error")
    if error:
        if error.get("code") == NO_DATA:
            return None
        raise ProviderUnavailable(f"{error.get('type')}: {error.get('message')}")
    return answer


def edition(album, exact=False):
    """The edition a Deezer album stands for, as editions.look_up() lists them."""
    return an_edition(album.get("link") or "", exact, title=album.get("title"),
                      artist=(album.get("artist") or {}).get("name"), date=album.get("release_date"),
                      label=album.get("label"), barcode=album.get("upc"), tracks=album.get("nb_tracks"),
                      format="Digital")


def _phrase(text):
    return '"' + text.replace('"', " ") + '"'


def fetch(album):
    """The editions of `album` ({"artist", "title", "barcode"}): the one its barcode names, else those
    of its artist and title. Raises ProviderUnavailable."""
    if album.get("barcode"):
        found = _get(f"album/upc:{album['barcode']}")
        if found and found.get("id"):
            return [edition(found, exact=True)]
    if not album.get("title") or not album.get("artist"):
        return []
    query = f"artist:{_phrase(album['artist'])} album:{_phrase(album['title'])}"
    found = (_get("search/album", {"q": query, "limit": DETAILED}) or {}).get("data") or []
    detailed = (_get(f"album/{a['id']}") for a in found[:DETAILED] if a.get("id"))
    return [edition(a) for a in detailed if a]
