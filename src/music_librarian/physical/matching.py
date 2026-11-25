"""Which album of the library each release of the collection is: the one holding its Discogs id,
else the one of the same artist and title once case, accents, punctuation, a leading "The" and
what the titles hold in brackets (edition, remaster) are set aside. An album that is only close
(one of the two the same, the other similar) is a question for the dashboard."""
import difflib
import re
import unicodedata
from dataclasses import dataclass, field

from music_librarian.physical.collection import NUMBERED

SIMILAR = 0.8
BRACKETS = re.compile(r"\([^)]*\)|\[[^\]]*\]")
ID, NAME, ANSWER = "id", "name", "answer"


def fold(text):
    return "".join(c for c in unicodedata.normalize("NFKD", (text or "").casefold())
                   if not unicodedata.combining(c))


def words(text):
    text = re.sub(r"[^\w]+", " ", fold(text).replace("&", " and ").replace("+", " and "))
    text = " ".join(text.replace("_", " ").split())
    return text[4:] if text.startswith("the ") else text


def artist_key(name):
    key = words(NUMBERED.sub("", (name or "").strip()).rstrip("*"))
    return "various" if key in ("various artists", "various", "va") else key


def title_key(title):
    title = (title or "").split(" = ")[0]
    return words(BRACKETS.sub(" ", title)) or words(title)


def album_key(artist, title):
    """What the dashboard's answers about an album are kept under."""
    return f"{fold(artist).strip()}\t{fold(title).strip()}"


def pair_key(release_id, key):
    """What the answer about a release being an album is kept under."""
    return f"{release_id}\t{key}"


@dataclass
class LibraryAlbum:
    folder: str    # relative to the root
    artist: str    # the album artist, else the artist
    title: str
    ids: set = field(default_factory=set)   # the Discogs release ids its tracks hold

    @property
    def key(self):
        return album_key(self.artist, self.title)


def release_artists(release):
    return {k for k in (artist_key(release["artist"]), *map(artist_key, release.get("artists") or ())) if k}


def close(a, b):
    if not a or not b:
        return False
    short, long = sorted((a, b), key=len)
    return (len(short) >= 4 and short in long) or difflib.SequenceMatcher(None, a, b).ratio() >= SIMILAR


@dataclass
class Matches:
    found: dict      # {folder: (release, ID, NAME or ANSWER)}
    doubtful: list   # [(release, Album)] not answered yet
    absent: list     # the releases no album is, nor may be


def match(releases, albums, answers):
    """The Matches of the collection's releases among the library's LibraryAlbum, `answers`
    holding {pair_key: True or False} of the dashboard's answers about the doubtful ones."""
    found, used = {}, set()
    by_id = {r["id"]: r for r in releases}
    for album in albums:
        held = sorted(album.ids & by_id.keys())
        if held:
            found[album.folder] = (by_id[held[0]], ID)
            used.add(held[0])
    by_name = {}
    for album in albums:
        by_name.setdefault((artist_key(album.artist), title_key(album.title)), []).append(album)
    for r in releases:
        for artist in release_artists(r):
            for album in by_name.get((artist, title_key(r["title"])), ()):
                if album.folder not in found:
                    found[album.folder] = (r, NAME)
                used.add(r["id"])
    by_artist, by_title = {}, {}
    for album in albums:
        if album.folder not in found:
            by_artist.setdefault(artist_key(album.artist), []).append(album)
            by_title.setdefault(title_key(album.title), []).append(album)
    doubtful, absent = [], []
    for r in releases:
        if r["id"] in used:
            continue
        artists, title = release_artists(r), title_key(r["title"])
        near = [a for k in artists for a in by_artist.get(k, ()) if close(title_key(a.title), title)]
        near += [a for a in by_title.get(title, ()) if any(close(artist_key(a.artist), k) for k in artists)]
        asked = False
        for album in {a.folder: a for a in near if a.folder not in found}.values():
            answer = answers.get(pair_key(r["id"], album.key))
            if answer is True:
                found[album.folder] = (r, ANSWER)
                used.add(r["id"])
            elif answer is None:
                doubtful.append((r, album))
                asked = True
        if r["id"] not in used and not asked:
            absent.append(r)
    return Matches(found, doubtful, absent)
