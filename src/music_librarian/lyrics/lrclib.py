"""LRCLIB (lrclib.net): open database of synced and plain lyrics."""
import logging
import os
from urllib.parse import quote

import requests

from .common import USER_AGENT, ProviderUnavailable
from .matching import by_length, drop_article, duration_close, float_duration

log = logging.getLogger(__name__)

LRCLIB_SITE = "https://lrclib.net"
LRCLIB_BASE = f"{LRCLIB_SITE}/api"

# LRCLIB takes 8-10s on busy evenings.
LRCLIB_TIMEOUT = int(os.getenv("LRCLIB_TIMEOUT", "15"))

# Versions offered for one track, the library's own text included. Each carries
# a full lyrics body into the cache, so this caps an entry's weight too.
MAX_VERSIONS = 5


def _version(payload, seconds):
    """One LRCLIB record in the shape the page cycles through.

    Timestamps only fit the recording they were made for, so the LRC is kept
    only while the record's own length matches the request's.
    """
    return {
        "lyrics": payload.get("plainLyrics"),
        "synced": payload.get("syncedLyrics") if duration_close(payload, seconds) else None,
        "album": payload.get("albumName"),
        "duration": payload.get("duration"),
    }


def _attempts(artist, title, album):
    """Search parameter sets for one track, most specific first.

    The album filter is dropped after being tried, since the search exists to
    forgive the album/duration mismatches `get` won't.
    """
    names = [(artist, title)]
    loose = (drop_article(artist), drop_article(title))
    if loose != (artist, title):
        names.append(loose)
    attempts = []
    for name_artist, name_title in names:
        base = {"artist_name": name_artist, "track_name": name_title}
        if album:
            attempts.append({**base, "album_name": album})
        attempts.append(base)
    return attempts


def _search_url(artist, title):
    """Browsable URL for this track on LRCLIB's own search page, for the diagnostic logs."""
    query = " ".join(part for part in (artist, title) if part)
    return f"{LRCLIB_SITE}/search/{quote(query, safe='')}"


def _candidates(results, seconds, search_params):
    """One search response's candidates, and those worth offering in order.

    The offer is the uploads of this track's length, synced first and plain
    behind, or empty when none is synced.
    """
    close = [c for c in results if duration_close(c, seconds)]
    synced = [c for c in close if c.get("syncedLyrics")]
    log.info(
        "lrclib: search (artist=%r, album=%r) returned %d candidate(s), %d of this length, "
        "%d of those synced",
        search_params.get("artist_name"), search_params.get("album_name"),
        len(results), len(close), len(synced),
    )
    if not synced:
        return close, []
    plain = [c for c in close if not c.get("syncedLyrics")]
    return close, by_length(synced, seconds) + by_length(plain, seconds)


def _search(artist, title, album, seconds, fallback):
    """Scan LRCLIB's `search` for a synced record of this very recording.

    Returns (record, others): the synced candidate closest to this track's
    length, or `fallback` (the `get` hit, when there was one) if the search
    turns up nothing better, and the rest of the set it was chosen from —
    other uploads of the same song, which the page offers to compare it
    against. Raises ProviderUnavailable if LRCLIB can't be reached.
    """
    others = []
    headers = {"User-Agent": USER_AGENT}
    for search_params in _attempts(artist, title, album):
        try:
            r = requests.get(
                f"{LRCLIB_BASE}/search",
                params=search_params,
                headers=headers,
                timeout=LRCLIB_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise ProviderUnavailable("lrclib") from exc
        if r.status_code != 200:
            log.info("lrclib: search returned HTTP %s", r.status_code)
            continue
        results = r.json() or []
        close, ranked = _candidates(results, seconds, search_params)
        if ranked:
            if fallback is not None and not any(c.get("id") == fallback.get("id") for c in ranked):
                ranked.append(fallback)
            return ranked[0], ranked[1:]
        pool = close or results
        if pool and not others:
            if fallback is None:
                fallback, others = pool[0], pool[1:]
            else:
                others = [c for c in pool if c.get("id") != fallback.get("id")]
    return fallback, others


def fetch(artist, title, album, duration):
    """Ask LRCLIB for a track, preferring a synced record of that very recording.

    Tries the exact `get`, then `search`, which forgives album and duration
    mismatches. LRCLIB stores lyrics per upload, so a `get` hit with no synced
    record of this length is only a fallback while the search looks for one. A synced candidate must
    match the track's duration; one that doesn't still serves as plain text.
    Raises ProviderUnavailable when LRCLIB can't be reached at all.
    """
    headers = {"User-Agent": USER_AGENT}

    params = {"artist_name": artist, "track_name": title}
    if album:
        params["album_name"] = album
    seconds = float_duration(duration)
    if seconds is not None:
        # LRCLIB's signature is indexed on whole seconds.
        params["duration"] = int(seconds)

    payload = None
    try:
        r = requests.get(f"{LRCLIB_BASE}/get", params=params, headers=headers, timeout=LRCLIB_TIMEOUT)
        if r.status_code == 200:
            payload = r.json()
        else:
            log.debug("lrclib: get returned HTTP %s, falling back to search", r.status_code)
    except requests.RequestException as exc:
        log.debug("lrclib: get failed (%s), falling back to search", exc)
        payload = None

    others = []
    if payload is None or not _version(payload, seconds)["synced"]:
        payload, others = _search(artist, title, album, seconds, payload)

    if not payload:
        log.info("lrclib: no record, catalogue search: %s", _search_url(artist, title))
        return None
    # Another recording's LRC would run against the wrong timeline, so only its
    # words are kept; the caller then shows them as plain lyrics.
    synced_text = _version(payload, seconds)["synced"]
    if payload.get("syncedLyrics") and not synced_text:
        log.info(
            "lrclib: %s/tracks/%s is %ss, this track is %ss - its timings dropped",
            LRCLIB_SITE, payload.get("id"), payload.get("duration"), int(seconds),
        )
    log.debug(
        "lrclib: record %s (%r, %ss), synced=%s",
        payload.get("id"), payload.get("albumName"), payload.get("duration"),
        bool(synced_text),
    )
    return {
        "lyrics": payload.get("plainLyrics"),
        "synced": synced_text,
        "meta": {
            "artist": payload.get("artistName"),
            "title": payload.get("trackName"),
            "album": payload.get("albumName"),
            "duration": payload.get("duration"),
        },
        "versions": [_version(c, seconds) for c in [payload] + others[:MAX_VERSIONS - 1]],
    }
