"""Musixmatch, through the endpoint of its desktop app."""
import logging
import os
import threading
import time

import requests

from .common import BROWSER_UA, ProviderUnavailable
from .matching import int_duration, matches_request

log = logging.getLogger(__name__)

MXM_BASE = "https://apic-desktop.musixmatch.com/ws/1.1"

_token_cache = {"value": None, "expires_at": 0}
_token_lock = threading.Lock()
_HEADERS = {
    "authority": "apic-desktop.musixmatch.com",
    "cookie": "AWSELB=0",
    "User-Agent": BROWSER_UA,
}


def _user_token():
    """Return a usable Musixmatch user token, cached in-process for 9 hours.

    `MUSIXMATCH_TOKEN` overrides the one the desktop web app is issued. Returns
    None when Musixmatch refuses a usable token, and raises ProviderUnavailable
    when it can't be reached.
    """
    with _token_lock:
        if _token_cache["value"] and _token_cache["expires_at"] > time.time():
            return _token_cache["value"]

        override = os.getenv("MUSIXMATCH_TOKEN")
        if override:
            _token_cache.update(value=override, expires_at=time.time() + 9 * 3600)
            return override

        try:
            r = requests.get(
                f"{MXM_BASE}/token.get",
                params={"app_id": "web-desktop-app-v1.0"},
                headers=_HEADERS,
                timeout=5,
            )
            token = (r.json().get("message", {}).get("body", {}) or {}).get("user_token")
        except requests.RequestException as exc:
            raise ProviderUnavailable("musixmatch") from exc
        except (ValueError, AttributeError) as exc:
            log.warning("musixmatch: token endpoint returned an unusable body (%s)", exc)
            return None

        # Musixmatch hands out a sentinel token when rate-limiting; reject it.
        if not token or token.startswith("UpgradeOnly"):
            log.warning("musixmatch: no usable token (rate-limited), provider skipped")
            return None
        _token_cache.update(value=token, expires_at=time.time() + 9 * 3600)
        return token


def _subtitle(subtitle_list, language):
    """Return a subtitle body from Musixmatch's list, skipping other languages.

    A track can carry several subtitles, translations among them; one that
    declares a language other than the lyrics' would swap out the words.
    """
    for entry in subtitle_list or []:
        subtitle = entry.get("subtitle", {}) if isinstance(entry, dict) else {}
        body = subtitle.get("subtitle_body")
        declared = subtitle.get("subtitle_language")
        if body and (not language or not declared or declared == language):
            return body
    return None


def _macro_calls(artist, title, album, duration, token):
    """Run the desktop macro and return its `macro_calls` map, or None.

    Raises ProviderUnavailable when Musixmatch can't be reached.
    """
    params = {
        "format": "json",
        "namespace": "lyrics_richsynched",
        "subtitle_format": "lrc",
        "app_id": "web-desktop-app-v1.0",
        "usertoken": token,
        "q_artist": artist,
        "q_track": title,
    }
    if album:
        params["q_album"] = album
    seconds = int_duration(duration)
    if seconds is not None:
        params["q_duration"] = seconds

    try:
        r = requests.get(
            f"{MXM_BASE}/macro.subtitles.get", params=params, headers=_HEADERS, timeout=6
        )
        body = r.json().get("message", {}).get("body", {})
    except requests.RequestException as exc:
        raise ProviderUnavailable("musixmatch") from exc
    except (ValueError, AttributeError) as exc:
        log.debug("musixmatch: unreadable response (%s)", exc)
        return None
    return body.get("macro_calls", {}) if isinstance(body, dict) else {}


def fetch(artist, title, album, duration):
    token = _user_token()
    if not token:
        return None
    calls = _macro_calls(artist, title, album, duration, token)
    if calls is None:
        return None

    def _body(call):
        node = calls.get(call, {})
        body = node.get("message", {}).get("body") if isinstance(node, dict) else None
        return body if isinstance(body, dict) else {}

    track = _body("matcher.track.get").get("track", {})
    meta = {
        "artist": track.get("artist_name"),
        "title": track.get("track_name"),
        "album": track.get("album_name"),
        "duration": track.get("track_length"),
    }
    # Musixmatch's matcher is fuzzy and answers even when nothing really fits,
    # so a track it doesn't carry comes back as the nearest song it does.
    if not matches_request(meta, artist, title, duration):
        log.info(
            "musixmatch: matched %r by %r (%ss), not %r by %r (%ss) - dropped",
            meta["title"], meta["artist"], int_duration(meta["duration"]),
            title, artist, int_duration(duration),
        )
        return None

    lyrics_node = _body("track.lyrics.get").get("lyrics", {})
    lyrics = lyrics_node.get("lyrics_body") or None
    synced = _subtitle(
        _body("track.subtitles.get").get("subtitle_list"), lyrics_node.get("lyrics_language")
    )

    if lyrics or synced:
        return {"lyrics": lyrics, "synced": synced, "meta": meta}
    return None
