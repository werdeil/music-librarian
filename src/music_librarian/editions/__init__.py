"""The editions of an album looked up online, by hand from its page only: providers asked in
`EDITION_PROVIDERS` order, the first that answers wins. Nothing is written."""
import logging
import os

from music_librarian.editions import musicbrainz
from music_librarian.editions.common import ProviderUnavailable
from music_librarian.lyrics.matching import fold_name

log = logging.getLogger(__name__)

# A provider takes the album ({"artist", "title", "mbid", "barcode"}) and returns its editions:
# {"title", "artist", "date", "country", "label", "catalog", "barcode", "tracks", "format", "url",
# "exact"}, exact when the album's MBID or barcode named it.
PROVIDERS = {"musicbrainz": musicbrainz.fetch}
NAMES = {"musicbrainz": "MusicBrainz"}
DEFAULT_PROVIDER_ORDER = "musicbrainz"


def enabled_providers():
    """The ordered provider list of `EDITION_PROVIDERS`; unknown names are ignored."""
    raw = os.getenv("EDITION_PROVIDERS", DEFAULT_PROVIDER_ORDER)
    names = [n.strip().lower() for n in raw.split(",") if n.strip()]
    return [(n, PROVIDERS[n]) for n in names if n in PROVIDERS]


def album_of(snapshots, typed=None):
    """The album the metadata cache snapshots of its tracks describe, as the providers take it, with
    the artist and title of `typed` where they are not blank, plus "tracks", its number of tracks."""
    def first(*keys):
        for snap in snapshots:
            easy = {k.lower(): v for k, v in (snap or {}).get("tags", {}).items()}
            value = next((v.strip() for k in keys for v in easy.get(k, []) if v.strip()), "")
            if value:
                return value
        return ""
    album = {"artist": first("albumartist", "album artist", "artist"), "title": first("album"),
             "mbid": first("musicbrainz_albumid"), "barcode": first("barcode"), "tracks": len(snapshots)}
    album.update({k: v.strip() for k, v in (typed or {}).items() if v and v.strip()})
    return album


def ranked(editions, album):
    """The editions, those the album's MBID or barcode named first, then those of its number of tracks,
    then those of its very title, each kind the earliest first; each with "year" and "matches"."""
    title = fold_name(album.get("title"))
    out = [dict(e, year=e["date"][:4] if e["date"][:4].isdigit() else "",
                matches=e["tracks"] == album["tracks"]) for e in editions]
    return sorted(out, key=lambda e: (not e["exact"], not e["matches"], fold_name(e["title"]) != title,
                                      not e["year"], e["date"]))


def look_up(album):
    """(the editions of `album` ranked, the provider that answered or why none did: "no metadata",
    "none", "unavailable")."""
    if not (album.get("mbid") or album.get("barcode") or (album.get("artist") and album.get("title"))):
        return [], "no metadata"
    providers = enabled_providers()
    unreachable = 0
    for name, provider in providers:
        try:
            found = provider(album)
        except ProviderUnavailable as exc:
            unreachable += 1
            log.warning("editions: %s unreachable (%s)", name, exc)
            continue
        if found:
            return ranked(found, album), name
    return [], "unavailable" if providers and unreachable == len(providers) else "none"
