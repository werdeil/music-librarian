"""The Discogs collection of the owner of $DISCOGS_TOKEN, every folder: read, a release added to it
or taken out of it, and the copy the last pass read, kept in the state folder for the dashboard,
which never asks Discogs for it."""
import json
import os
import re
import time
from pathlib import Path

import requests

from music_librarian.editions import discogs
from music_librarian.editions.common import TIMEOUT, ProviderUnavailable, get_json
from music_librarian.lyrics.common import USER_AGENT

API = "https://api.discogs.com"
PER_PAGE = 100
UNCATEGORIZED = 1   # the folder a release is added to
FILE = "physical-collection.json"
NUMBERED = re.compile(r"\s*\(\d+\)$")   # Discogs numbers the artists sharing a name: "Prince (2)"


class CollectionError(Exception):
    """The collection cannot be read: no token, or Discogs refusing it."""


def _headers():
    return {"Authorization": f"Discogs token={discogs.token()}"}


def _ask(path, params=None):
    found = get_json(API + path, params or {}, discogs.LAST, discogs.INTERVAL, _headers())
    if found is None:
        raise CollectionError(f"Discogs knows no {path}")
    return found


def artist_of(artists):
    """The credit of a release's artists as Discogs joins them, without their numbers."""
    out = ""
    for a in artists or []:
        out += NUMBERED.sub("", a.get("anv") or a.get("name") or "")
        join = (a.get("join") or "").strip()
        out += f" {join} " if join and join != "," else ", " if join else ""
    return re.sub(r"[\s,]+$", "", out).strip()


def release(item):
    """A release of the collection as a pass keeps it: {id, artist, artists, title, year, formats,
    instances: [[folder id, instance id]] of its copies}."""
    info = item.get("basic_information") or {}
    names = [NUMBERED.sub("", a.get("name") or "") for a in info.get("artists") or []]
    instances = [[item["folder_id"], item["instance_id"]]] if item.get("instance_id") else []
    return dict(id=str(info.get("id") or item.get("id")), artist=artist_of(info.get("artists")),
                instances=instances,
                artists=[n for n in names if n], title=info.get("title") or "",
                year=str(info.get("year") or "") if info.get("year") else "",
                formats=list(dict.fromkeys(f["name"] for f in info.get("formats") or [] if f.get("name"))))


def fetch():
    """(user name, [release]) of the token owner's collection, every folder, a release held twice
    once. Raises CollectionError, ProviderUnavailable."""
    if not discogs.token():
        raise CollectionError("$DISCOGS_TOKEN is not set")
    try:
        user = _ask("/oauth/identity").get("username")
    except ProviderUnavailable as e:
        if "401" in str(e) or "403" in str(e):
            raise CollectionError(f"Discogs refused the token ({e})") from None
        raise
    if not user:
        raise CollectionError("Discogs names no user for the token")
    found, page, pages = {}, 1, 1
    while page <= pages:
        answer = _ask(f"/users/{user}/collection/folders/0/releases",
                      {"page": page, "per_page": PER_PAGE, "sort": "added"})
        pages = int((answer.get("pagination") or {}).get("pages") or 1)
        for item in answer.get("releases") or []:
            r = release(item)
            found.setdefault(r["id"], {**r, "instances": []})["instances"] += r["instances"]
        page += 1
    return user, list(found.values())


def _send(method, path):
    """Ask Discogs to change the collection; raises ProviderUnavailable when it does not."""
    delay = discogs.LAST[0] + discogs.INTERVAL - time.monotonic()
    if delay > 0:
        time.sleep(delay)
    discogs.LAST[0] = time.monotonic()
    try:
        r = requests.request(method, API + path, headers={"User-Agent": USER_AGENT, **_headers()},
                             timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise ProviderUnavailable(str(exc)) from exc
    if r.status_code not in (200, 201, 204):
        raise ProviderUnavailable(f"HTTP {r.status_code}")


def add(user, release_id):
    """Add the release to the user's collection, in its Uncategorized folder."""
    _send("POST", f"/users/{user}/collection/folders/{UNCATEGORIZED}/releases/{release_id}")


def remove(user, held):
    """Take every copy of the release `held` out of the user's collection."""
    for folder, instance in held.get("instances") or []:
        _send("DELETE", f"/users/{user}/collection/folders/{folder}/releases/{held['id']}"
                        f"/instances/{instance}")


def keep(state_dir: Path, user, releases):
    """Keep the collection read for the dashboard."""
    path = Path(state_dir) / FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(dict(user=user, read=time.time(), releases=releases), ensure_ascii=False),
                    encoding="utf-8")
    os.replace(temp, path)


def kept(state_dir: Path):
    """{user, read, releases} of the collection the last pass read, None before any."""
    try:
        found = json.loads((Path(state_dir) / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return found if isinstance(found, dict) and isinstance(found.get("releases"), list) else None
