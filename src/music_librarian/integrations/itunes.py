"""iTunes Search API: the genre Apple Music files an album under."""
import os
import re
import time

import requests

from music_librarian.core.lock import STOP
from music_librarian.lyrics.common import USER_AGENT
from music_librarian.lyrics.matching import fold_name

SEARCH = "https://itunes.apple.com/search"
TIMEOUT = 15
# Apple allows about 20 searches a minute per address.
INTERVAL = 3.0
# Apple's own suffixes on single and EP releases
_RELEASE_SUFFIX = re.compile(r"\s+-\s+(single|ep)$", re.IGNORECASE)


class Unavailable(Exception):
    """The API could not be reached or refused the request."""


class Client:
    def __init__(self, country="us", interval=INTERVAL):
        self.country, self.interval = country, interval
        self.last = 0.0

    def _wait(self):
        delay = self.last + self.interval - time.monotonic()
        if delay > 0:
            STOP.wait(delay)
        self.last = time.monotonic()

    def search(self, artist, album):
        """The album results for `artist album`. Raises Unavailable."""
        self._wait()
        params = {"term": f"{artist} {album}", "entity": "album", "country": self.country, "limit": 25}
        try:
            r = requests.get(SEARCH, params=params, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise Unavailable(str(exc)) from exc
        if r.status_code != 200:
            raise Unavailable(f"HTTP {r.status_code}")
        try:
            return r.json().get("results", [])
        except ValueError as exc:
            raise Unavailable("not JSON") from exc

    def album_genre(self, artist, album):
        """Apple's primary genre of this album, or None when no result has this artist and title."""
        want = (fold_name(artist), fold_name(album))
        for result in self.search(artist, album):
            title = _RELEASE_SUFFIX.sub("", result.get("collectionName") or "")
            if (fold_name(result.get("artistName")), fold_name(title)) == want:
                return result.get("primaryGenreName") or None
        return None


def from_env():
    """A client when $GENRE_LOOKUP is itunes, else None."""
    if os.environ.get("GENRE_LOOKUP", "").strip().lower() != "itunes":
        return None
    return Client(os.environ.get("ITUNES_COUNTRY", "").strip().lower() or "us")
