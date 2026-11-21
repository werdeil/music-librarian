"""What the edition providers share: how they fail, how they are asked, what they answer."""
import time

import requests

from music_librarian.lyrics.common import USER_AGENT

TIMEOUT = 15


class ProviderUnavailable(Exception):
    """A provider could not be reached, as opposed to having no edition to give."""


def get_json(url, params, last, interval, headers=None):
    """The JSON `url` answers with, None for what it does not know (400, 404), asked at most once
    every `interval` seconds, `last` holding when it last was ([monotonic time]). Raises
    ProviderUnavailable."""
    delay = last[0] + interval - time.monotonic()
    if delay > 0:
        time.sleep(delay)
    last[0] = time.monotonic()
    try:
        r = requests.get(url, params=params, headers={"User-Agent": USER_AGENT, **(headers or {})},
                         timeout=TIMEOUT)
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


def edition(url, exact=False, **found):
    """An edition as editions.look_up() lists them, its fields not `found` left blank."""
    blank = dict(title="", artist="", date="", country="", label="", catalog="", barcode="", tracks=None,
                 format="")
    return {**blank, **{k: v for k, v in found.items() if v}, "url": url, "exact": exact}
