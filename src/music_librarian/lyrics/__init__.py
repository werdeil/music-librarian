"""Web fallback for lyrics.

Results live in a process-local in-memory cache. Providers are tried in
`LYRICS_PROVIDERS` order: the first synced result wins, else the first plain one.
"""
import logging
import os
import time

from music_librarian.lyrics import cache, lrclib, musixmatch, netease
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

# The cache key includes client-supplied fields: longer ones are refused.
MAX_FIELD_LEN = 512


def _elapsed_ms(started):
    return int((time.monotonic() - started) * 1000)


def _enabled_providers():
    """Resolve the ordered provider list from `LYRICS_PROVIDERS`.

    Unknown names are ignored.
    """
    raw = os.getenv("LYRICS_PROVIDERS", DEFAULT_PROVIDER_ORDER)
    names = [n.strip().lower() for n in raw.split(",") if n.strip()]
    return [(n, PROVIDERS[n]) for n in names if n in PROVIDERS]


def _log_rejected(name, meta, artist, title, duration):
    log.info(
        "lyrics: %s answered with %r by %r (%ss), not %r by %r (%ss) - rejected",
        name, meta.get("title"), meta.get("artist"), int_duration(meta.get("duration")),
        title, artist, int_duration(duration),
    )


def _search_providers(artist, title, album, duration, verify):
    """Try each enabled provider in order and return the first usable result.

    A plain-only result is held while the later providers able to sync are asked
    for a synced one, unless `verify` is set: the batch CLI only stores plain text.
    Same shape as fetch_lyrics' return value, without any caching: "unavailable"
    means not one provider answered, which the caller must not confuse with a
    search that ran and came up empty.
    """
    rejected = False
    unreachable = 0
    plain = None
    providers = _enabled_providers()
    if not providers:
        # An empty list is how an operator turns the web search off, so this is
        # an outcome to state once per search, not a fault to warn about.
        log.info("lyrics: web search disabled, LYRICS_PROVIDERS=%r resolves to no provider",
                 os.getenv("LYRICS_PROVIDERS"))
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
            # A misbehaving provider must not break the chain.
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
        log.debug(
            "lyrics: %s matched in %d ms (synced=%s, plain=%s)",
            name, _elapsed_ms(started), bool(found.get("synced")), bool(found.get("lyrics")),
        )
        result = {"lyrics": found.get("lyrics"), "synced": found.get("synced"), "source": name}
        if found.get("versions"):
            result["versions"] = found["versions"]
        if result["synced"] or verify:
            return result
        if plain is None:
            log.info("lyrics: %s has plain lyrics only, asking the next providers for synced ones", name)
            plain = result

    if plain:
        return plain
    if unreachable and unreachable == len(providers):
        return {"lyrics": None, "synced": None, "source": "unavailable"}
    return {"lyrics": None, "synced": None, "source": "rejected" if rejected else "none"}


def fetch_lyrics(track_id, artist, title, album=None, duration=None, force=False, verify=False):
    """Resolve lyrics for the current track from the web, with caching.

    Tries each enabled provider in order and keeps the first synced result, else
    the first plain one.
    Returns a dict {"lyrics": str|None, "synced": str|None, "source": str}.
    `source` is the winning provider name (kept across cache hits), "none" when
    nothing was found, "rejected"
    when a candidate came back but failed verification, or "unavailable" when no
    provider could be reached — the one outcome that is not cached, so a search
    is retried as soon as they answer again.

    A provider that found several uploads of the song also returns "versions"
    for the page to cycle through: the winner first, then the rest synced
    before plain, at most lrclib.MAX_VERSIONS long.

    With `verify=True` (the batch CLI, which writes into tags), a result is only
    accepted when its own metadata matches the requested track (see
    matches_request).
    """
    if not title or not artist:
        log.info("lyrics: search skipped, track %s has no artist or title", track_id)
        return {"lyrics": None, "synced": None, "source": "none"}
    if any(f and len(str(f)) > MAX_FIELD_LEN for f in (track_id, artist, title, album)):
        log.info("lyrics: search skipped for track %s, a metadata field exceeds %d chars",
                 track_id, MAX_FIELD_LEN)
        return {"lyrics": None, "synced": None, "source": "none"}

    # Streams like a Deezer flow keep one track_id across songs; `verify` is in the
    # key since a lenient and a verified lookup can differ.
    cache_key = f"{track_id or ''}|{artist}|{title}|{int(bool(verify))}"
    if not force:
        cached = cache.get(cache_key)
        if cached is not None:
            log.info("lyrics: cache hit for %r by %r (source=%s), no search made",
                     title, artist, cached["source"])
            return dict(cached)

    started = time.monotonic()
    log.debug("lyrics: searching %r by %r (album=%r, duration=%s, verify=%s)",
              title, artist, album, duration, verify)
    result = _search_providers(artist, title, album, duration, verify)
    log.info(
        "lyrics: %r by %r -> %s (synced=%s, plain=%s) in %d ms",
        title, artist, result["source"], bool(result["synced"]), bool(result["lyrics"]), _elapsed_ms(started),
    )
    if result["source"] == "unavailable":
        # Nothing was learned about the track, so caching this as a miss would
        # fuse every search for cache.TTL_MISS over a passing outage.
        return result

    hit = bool(result["lyrics"] or result["synced"])
    cache.put(cache_key, dict(result), cache.TTL_HIT if hit else cache.TTL_MISS)
    return result
