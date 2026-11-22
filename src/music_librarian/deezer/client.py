"""Every request to Deezer, its pages, its APIs and its streams, made as the web player makes them."""
import threading
import time

import requests

INTERVAL = 0.5
TIMEOUT = 30
# Deezer answers a default client's user agent with an error page.
HEADERS = {
    "Origin": "https://www.deezer.com",
    "Referer": "https://www.deezer.com/",
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/68.0.3440.106 Safari/537.36"),
    "X-Requested-With": "XMLHttpRequest",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "*/*",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

# Deezer throttles an account, not a connection: the interval holds across every client of the process.
_lock = threading.Lock()
_last = [float("-inf")]


class DeezerError(Exception):
    """Deezer could not be reached, or answered something unusable."""


class Client:
    """Requests sent with the `arl` cookie of a logged-in session, at least INTERVAL seconds apart."""

    def __init__(self, arl, session=None, clock=time.monotonic, sleep=time.sleep):
        self._arl = arl
        self.session = session or requests.Session()
        self.clock, self.sleep = clock, sleep

    def __repr__(self):
        return "Client()"

    def _wait_turn(self):
        with _lock:
            delay = _last[0] + INTERVAL - self.clock()
            if delay > 0:
                self.sleep(delay)
            _last[0] = self.clock()

    def request(self, method, url, cookie=True, **kwargs):
        """The response to `url`, whatever its status; one transport error is tried again.
        Without `cookie`, the session's arl is not sent (a cover on a public CDN). Raises DeezerError."""
        kwargs.setdefault("timeout", TIMEOUT)
        cookies = {"arl": self._arl} if cookie else None
        for attempt in (1, 2):
            self._wait_turn()
            try:
                return self.session.request(method, url, headers=HEADERS, cookies=cookies, **kwargs)
            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt == 2:
                    raise DeezerError(f"{type(exc).__name__} on {_host(url)}") from exc
            except requests.RequestException as exc:
                raise DeezerError(f"{type(exc).__name__} on {_host(url)}") from exc
        raise AssertionError("unreachable")

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)


def _host(url):
    """The host of `url` alone: a query can carry the license token."""
    return url.split("://", 1)[-1].split("/", 1)[0]
