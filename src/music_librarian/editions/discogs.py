"""Discogs: the releases its users catalogue, the most precise on editions (country, label, catalog
number, format). It needs a personal access token, $DISCOGS_TOKEN: without one, it is not asked."""
import os

from music_librarian.editions.common import edition as an_edition, get_json

SEARCH = "https://api.discogs.com/database/search"
RELEASE = "https://api.discogs.com/releases/"
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


def of_release(found):
    """The edition of a Discogs release as /releases/<id> gives it, the one its id named."""
    labels = found.get("labels") or []
    named = lambda key: [x.get("name") for x in found.get(key) or [] if x.get("name")]
    barcode = next((i.get("value") for i in found.get("identifiers") or [] if i.get("type") == "Barcode"), "")
    tracks = sum(1 for t in found.get("tracklist") or [] if t.get("type_", "track") == "track")
    return an_edition(found.get("uri") or f"{SITE}/release/{found.get('id')}", True,
                      title=found.get("title"), artist=" & ".join(named("artists")),
                      date=found.get("released") or str(found.get("year") or ""),
                      country=found.get("country"), label="; ".join(dict.fromkeys(named("labels"))),
                      catalog=next((lb.get("catno") for lb in labels if lb.get("catno")), ""),
                      barcode=barcode, tracks=tracks or None,
                      format="; ".join(dict.fromkeys(named("formats"))))


def release(release_id):
    """The edition of the release `release_id`, None when Discogs knows none or without a token.
    Raises ProviderUnavailable."""
    if not token():
        return None
    found = get_json(RELEASE + release_id, {}, LAST, INTERVAL, {"Authorization": f"Discogs token={token()}"})
    return of_release(found) if found else None


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
