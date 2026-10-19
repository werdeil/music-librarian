"""NetEase Cloud Music: LRC lyrics, strong on Asian catalogues."""
import logging
import re

import requests

from .common import BROWSER_UA, ProviderUnavailable
from .matching import by_length, float_duration, int_duration, matches_request

log = logging.getLogger(__name__)

NETEASE_BASE = "https://music.163.com/api"

_HEADERS = {"User-Agent": BROWSER_UA, "Referer": "https://music.163.com/"}
_LRC_TIMESTAMP_RE = re.compile(r"^\[\d+:\d{2}", re.MULTILINE)
# NetEase prepends the song credits as timed lines, in Chinese whatever the song's language.
_CREDIT_RE = re.compile(
    r"^\[[\d:.]+\]\s*(?:作词|作詞|作曲|编曲|編曲|制作人|製作人|监制|混音|母带|录音|和声|出品|词|曲)\s*[:：]"
)


def _song_meta(song):
    artists = [a.get("name") for a in song.get("ar") or [] if isinstance(a, dict)]
    album = song.get("al") if isinstance(song.get("al"), dict) else {}
    millis = song.get("dt")
    return {
        "artists": [a for a in artists if a],
        "title": song.get("name"),
        "album": album.get("name"),
        "duration": millis / 1000 if isinstance(millis, (int, float)) and millis > 0 else None,
    }


def _pick(songs, artist, title, duration):
    """Return (id, meta) of the search hit that is this recording, nearest in length, or None.

    The search is free-text and always answers with its nearest songs, so each
    hit must pass matches_request against one of its credited artists.
    """
    matches = []
    for song in songs:
        if not isinstance(song, dict) or not song.get("id"):
            continue
        meta = _song_meta(song)
        credited = next(
            (a for a in meta["artists"]
             if matches_request(dict(meta, artist=a), artist, title, duration)), None
        )
        if credited:
            matches.append({
                "id": song["id"], "artist": credited, "title": meta["title"],
                "album": meta["album"], "duration": meta["duration"],
            })
    if not matches:
        return None
    best = by_length(matches, float_duration(duration))[0]
    return best.pop("id"), best


def _search(artist, title):
    """Return NetEase's song hits for `artist title`, or None when it gave no list.

    Raises ProviderUnavailable when NetEase can't be reached.
    """
    try:
        r = requests.get(
            f"{NETEASE_BASE}/cloudsearch/pc",
            params={"s": f"{artist} {title}", "type": 1, "limit": 10, "offset": 0},
            headers=_HEADERS,
            timeout=6,
        )
        if r.status_code != 200:
            log.info("netease: search returned HTTP %s", r.status_code)
            return None
        payload = r.json()
    except requests.RequestException as exc:
        raise ProviderUnavailable("netease") from exc
    except ValueError as exc:
        log.debug("netease: unreadable search response (%s)", exc)
        return None
    result = payload.get("result") if isinstance(payload, dict) else None
    # A blocked search still answers 200, with an error code or an encrypted result string.
    if not isinstance(result, dict):
        log.info("netease: search answered without a song list (code %s)",
                 payload.get("code") if isinstance(payload, dict) else None)
        return None
    return result.get("songs") or []


def _lyric(song_id):
    """Return a NetEase song's LRC with its credit lines dropped, or "" when it has none.

    Raises ProviderUnavailable when NetEase can't be reached.
    """
    try:
        r = requests.get(
            f"{NETEASE_BASE}/song/lyric",
            params={"id": song_id, "lv": 1, "kv": 1, "tv": -1},
            headers=_HEADERS,
            timeout=6,
        )
        if r.status_code != 200:
            log.info("netease: lyrics of song %s returned HTTP %s", song_id, r.status_code)
            return ""
        text = ((r.json().get("lrc") or {}).get("lyric") or "").strip()
    except requests.RequestException as exc:
        raise ProviderUnavailable("netease") from exc
    except (ValueError, AttributeError) as exc:
        log.debug("netease: unreadable lyrics response (%s)", exc)
        return ""
    lines = [line for line in text.splitlines() if not _CREDIT_RE.match(line.strip())]
    return "\n".join(lines).strip()


def fetch(artist, title, _album, duration):
    """Ask NetEase Cloud Music for a track's LRC.

    Searches `artist title`, keeps the hit that verifies against the request and
    fetches its lyrics; an LRC without timestamps is returned as plain text.
    Raises ProviderUnavailable when NetEase can't be reached.
    """
    songs = _search(artist, title)
    picked = _pick(songs or [], artist, title, duration)
    if not picked:
        if songs:
            log.info("netease: %d search results, none is %r by %r", len(songs), title, artist)
        return None
    song_id, meta = picked

    text = _lyric(song_id)
    log.debug("netease: song %s (%r, %ss), %d characters",
              song_id, meta["album"], int_duration(meta["duration"]), len(text))
    if not text:
        log.info("netease: song %s (%r, %ss) has no lyrics",
                 song_id, meta["album"], int_duration(meta["duration"]))
        return None
    if _LRC_TIMESTAMP_RE.search(text):
        return {"lyrics": None, "synced": text, "meta": meta}
    return {"lyrics": text, "synced": None, "meta": meta}
