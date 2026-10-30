"""Lyrics lookup on the web: providers tried in `LYRICS_PROVIDERS` order, the first usable answer wins."""
import logging
import os
import time

from music_librarian.lyrics import lrclib, musixmatch, netease
from music_librarian.lyrics.common import ProviderUnavailable
from music_librarian.lyrics.matching import int_duration, matches_request

log = logging.getLogger(__name__)

# A provider takes (artist, title, album, duration) and returns None or {"lyrics", "synced", "meta"},
# meta being the matched candidate's own artist/title/album/duration, for matches_request.
PROVIDERS = {
    "lrclib": lrclib.fetch,
    "musixmatch": musixmatch.fetch,
    "netease": netease.fetch,
}
DEFAULT_PROVIDER_ORDER = "lrclib,netease"


def _elapsed_ms(started):
    return int((time.monotonic() - started) * 1000)


def enabled_providers():
    """Resolve the ordered provider list from `LYRICS_PROVIDERS`; unknown names are ignored."""
    raw = os.getenv("LYRICS_PROVIDERS", DEFAULT_PROVIDER_ORDER)
    names = [n.strip().lower() for n in raw.split(",") if n.strip()]
    return [(n, PROVIDERS[n]) for n in names if n in PROVIDERS]


def _log_rejected(name, meta, artist, title, duration):
    log.info(
        "lyrics: %s answered with %r by %r (%ss), not %r by %r (%ss) - rejected",
        name, meta.get("title"), meta.get("artist"), int_duration(meta.get("duration")),
        title, artist, int_duration(duration),
    )


def fetch_lyrics(artist, title, album=None, duration=None, verify=False):
    """Ask each enabled provider in turn and return the first usable answer.

    Returns {"lyrics": str|None, "synced": str|None, "source": str}: `source` is the
    provider that answered, "none" when nothing was found, "rejected" when a candidate
    failed verification, or "unavailable" when no provider could be reached or none is
    enabled, which says nothing about what the providers carry.

    With `verify=True`, an answer is only accepted when its own metadata matches the
    requested track (see matches_request).
    """
    if not title or not artist:
        return {"lyrics": None, "synced": None, "source": "none"}
    providers = enabled_providers()
    if not providers:
        log.info("lyrics: web search disabled, LYRICS_PROVIDERS=%r resolves to no provider",
                 os.getenv("LYRICS_PROVIDERS"))
    rejected = False
    unreachable = 0
    for name, provider in providers:
        started = time.monotonic()
        try:
            found = provider(artist, title, album, duration)
        except ProviderUnavailable as exc:
            unreachable += 1
            log.warning("lyrics: %s unreachable after %d ms (%s)",
                        name, _elapsed_ms(started), exc.__cause__ or exc)
            continue
        except Exception as exc:
            log.warning("lyrics: %s failed after %d ms (%s: %s)",
                        name, _elapsed_ms(started), type(exc).__name__, exc)
            continue
        if not (found and (found.get("lyrics") or found.get("synced"))):
            log.info("lyrics: %s has no match (%d ms)", name, _elapsed_ms(started))
            continue
        if verify and not matches_request(found.get("meta"), artist, title, duration):
            _log_rejected(name, found.get("meta") or {}, artist, title, duration)
            rejected = True
            continue
        log.debug("lyrics: %s matched in %d ms", name, _elapsed_ms(started))
        return {"lyrics": found.get("lyrics"), "synced": found.get("synced"), "source": name}

    if unreachable == len(providers):
        return {"lyrics": None, "synced": None, "source": "unavailable"}
    return {"lyrics": None, "synced": None, "source": "rejected" if rejected else "none"}
