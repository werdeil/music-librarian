"""MusicBrainz (musicbrainz.org): the releases of an album, each an edition with its date, country and
label."""
import time

import requests

from music_librarian.editions.common import ProviderUnavailable
from music_librarian.lyrics.common import USER_AGENT

API = "https://musicbrainz.org/ws/2"
SITE = "https://musicbrainz.org/release/"
TIMEOUT = 15
INTERVAL = 1.0   # MusicBrainz allows a client one request a second
SEARCHED = 50
_last = [0.0]


def _get(path, params):
    """The JSON MusicBrainz answers `path` with, None for a release it does not know. Raises
    ProviderUnavailable."""
    delay = _last[0] + INTERVAL - time.monotonic()
    if delay > 0:
        time.sleep(delay)
    _last[0] = time.monotonic()
    try:
        r = requests.get(f"{API}/{path}", params={**params, "fmt": "json"},
                         headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise ProviderUnavailable(str(exc)) from exc
    if r.status_code in (400, 404):
        return None
    if r.status_code != 200:
        raise ProviderUnavailable(f"HTTP {r.status_code}")
    try:
        return r.json()
    except ValueError as exc:
        raise ProviderUnavailable("not JSON") from exc


def _phrase(text):
    """`text` as a quoted phrase of a Lucene query."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def edition(release, exact=False):
    """The edition a MusicBrainz release stands for, as editions.look_up() lists them."""
    labels = [li for li in release.get("label-info") or [] if li]
    names = [li["label"]["name"] for li in labels if (li.get("label") or {}).get("name")]
    media = release.get("media") or []
    credited = release.get("artist-credit") or []
    return {
        "title": release.get("title") or "",
        "artist": "".join(c.get("name", "") + c.get("joinphrase", "") for c in credited),
        "date": release.get("date") or "",
        "country": release.get("country") or "",
        "label": "; ".join(dict.fromkeys(names)),
        "catalog": next((li["catalog-number"] for li in labels if li.get("catalog-number")), ""),
        "barcode": release.get("barcode") or "",
        "tracks": release.get("track-count") or sum(m.get("track-count") or 0 for m in media) or None,
        "format": "; ".join(dict.fromkeys(m["format"] for m in media if m.get("format"))),
        "url": SITE + release["id"],
        "exact": exact,
    }


def fetch(album):
    """The editions of `album` ({"artist", "title", "mbid", "barcode"}): the release its MBID names,
    else those of its barcode, else those of its artist and title, exact for the first two. Raises
    ProviderUnavailable."""
    if album.get("mbid"):
        release = _get(f"release/{album['mbid']}", {"inc": "labels artist-credits media"})
        if release and release.get("id"):
            return [edition(release, exact=True)]
    if album.get("barcode"):
        found = _get("release", {"query": f"barcode:{_phrase(album['barcode'])}", "limit": SEARCHED})
        if found and found.get("releases"):
            return [edition(r, exact=True) for r in found["releases"]]
    if not album.get("title") or not album.get("artist"):
        return []
    query = f"release:{_phrase(album['title'])} AND artist:{_phrase(album['artist'])}"
    found = _get("release", {"query": query, "limit": SEARCHED}) or {}
    return [edition(r) for r in found.get("releases") or []]
