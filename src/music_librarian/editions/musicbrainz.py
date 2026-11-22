"""MusicBrainz (musicbrainz.org): the releases of an album, each an edition with its date, country and
label."""
from music_librarian.editions.common import edition as an_edition, get_json

API = "https://musicbrainz.org/ws/2"
SITE = "https://musicbrainz.org/release/"
INTERVAL = 1.0   # MusicBrainz allows a client one request a second
SEARCHED = 50
_last = [0.0]


def _get(path, params):
    """The JSON MusicBrainz answers `path` with, None for a release it does not know. Raises
    ProviderUnavailable."""
    return get_json(f"{API}/{path}", {**params, "fmt": "json"}, _last, INTERVAL)


def _phrase(text):
    """`text` as a quoted phrase of a Lucene query."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def edition(release, exact=False):
    """The edition a MusicBrainz release stands for, as editions.look_up() lists them."""
    labels = [li for li in release.get("label-info") or [] if li]
    names = [li["label"]["name"] for li in labels if (li.get("label") or {}).get("name")]
    media = release.get("media") or []
    credited = release.get("artist-credit") or []
    return an_edition(
        SITE + release["id"], exact, title=release.get("title"),
        artist="".join(c.get("name", "") + c.get("joinphrase", "") for c in credited),
        date=release.get("date"), country=release.get("country"), label="; ".join(dict.fromkeys(names)),
        catalog=next((li["catalog-number"] for li in labels if li.get("catalog-number")), ""),
        barcode=release.get("barcode"),
        tracks=release.get("track-count") or sum(m.get("track-count") or 0 for m in media) or None,
        format="; ".join(dict.fromkeys(m["format"] for m in media if m.get("format"))))


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
