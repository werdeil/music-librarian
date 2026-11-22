"""iTunes Search API: the albums Apple sells, each an edition with its release date, as Apple Music
files it."""
import os

from music_librarian.editions.common import edition as an_edition, get_json
from music_librarian.integrations.itunes import same_album

SEARCH = "https://itunes.apple.com/search"
LOOKUP = "https://itunes.apple.com/lookup"
INTERVAL = 3.0   # Apple allows about 20 searches a minute per address
_last = [0.0]


def _country():
    return os.environ.get("ITUNES_COUNTRY", "").strip().lower() or "us"


def edition(result, exact=False):
    """The edition an iTunes album result stands for, as editions.look_up() lists them."""
    return an_edition(result.get("collectionViewUrl") or "", exact, title=result.get("collectionName"),
                      artist=result.get("artistName"), date=(result.get("releaseDate") or "")[:10],
                      country=result.get("country"), label=result.get("copyright"),
                      tracks=result.get("trackCount"), format="Digital")


def _albums(answer):
    return [r for r in (answer or {}).get("results") or [] if r.get("collectionType") == "Album"]


def fetch(album):
    """The editions of `album` ({"artist", "title", "barcode"}) in the store of $ITUNES_COUNTRY: the one
    its barcode names, else those of its very artist and title, the search giving any album near its
    words. Raises ProviderUnavailable."""
    if album.get("barcode"):
        found = _albums(get_json(LOOKUP, {"upc": album["barcode"], "country": _country(), "entity": "album"},
                                 _last, INTERVAL))
        if found:
            return [edition(r, exact=True) for r in found]
    if not album.get("title") or not album.get("artist"):
        return []
    term = f"{album['artist']} {album['title']}"
    found = get_json(SEARCH, {"term": term, "entity": "album", "country": _country(), "limit": 25},
                     _last, INTERVAL)
    return [edition(r) for r in _albums(found) if same_album(r, album["artist"], album["title"])]
