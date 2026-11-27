"""Which album of the library each release of the collection is: the one holding its Discogs id,
else the one of the same artist and title once case, accents, punctuation, a leading "The" and
what the titles hold in brackets (edition, remaster) are set aside, a compilation's title alone
for a release of Various. An album only close (one of the two the same, the other similar, or
both the same but for spaces, letters such as ø or a bracket naming a remix or a live) is a
question for the dashboard, its tags perhaps to correct."""
import difflib
import re
import unicodedata
from dataclasses import dataclass, field

from music_librarian.physical.collection import NUMBERED

SIMILAR = 0.8
BRACKETS = re.compile(r"\([^)]*\)|\[[^\]]*\]")
OTHER_RECORD = re.compile(r"remix|\bmix|live|acoustic|unplugged|instrumental|demo|karaoke|\bep\b|\be p\b"
                          r"|single|\bdub\b|session|rarit|b side|cover")
LETTERS = str.maketrans({"ø": "o", "æ": "ae", "œ": "oe", "ß": "ss", "ł": "l", "đ": "d", "þ": "th"})
ID, NAME, ANSWER, LINK = "id", "name", "answer", "link"


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
    """The title's words, but those of a bracket that names an edition (not a remix, a live…)."""
    title = (title or "").split(" = ")[0]
    kept = BRACKETS.sub(lambda m: m[0] if OTHER_RECORD.search(words(m[0])) else " ", title)
    return words(kept) or words(title)


def loose(key):
    """A key as a close match compares it: its letters alone, ø as o."""
    return words(BRACKETS.sub(" ", key)).translate(LETTERS).replace(" ", "") if key else ""


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
    compilation: bool = False

    @property
    def key(self):
        return album_key(self.artist, self.title)


def album_artists(album):
    return {artist_key(album.artist)} | ({"various"} if album.compilation else set())


def release_artists(release):
    return {k for k in (artist_key(release["artist"]), *map(artist_key, release.get("artists") or ())) if k}


def close(a, b):
    if not a or not b:
        return False
    short, long = sorted((a, b), key=len)
    return (len(short) >= 4 and short in long) or difflib.SequenceMatcher(None, a, b).ratio() >= SIMILAR


@dataclass
class Matches:
    found: dict      # {folder: (release, ID, LINK, NAME or ANSWER)}
    doubtful: list   # [(release, Album)] not answered yet
    absent: list     # the releases no album is, nor may be


def match(releases, albums, answers, links=None):
    """The Matches of the collection's releases among the library's LibraryAlbum, `answers`
    holding {pair_key: True or False} of the dashboard's answers about the doubtful ones, `links`
    {release id: folder, or "" for none} the albums the dashboard picked for the absent ones."""
    found, used = {}, set()
    by_id = {r["id"]: r for r in releases}
    for album in albums:
        held = sorted(album.ids & by_id.keys())
        if held:
            found[album.folder] = (by_id[held[0]], ID)
            used.add(held[0])
    folders = {album.folder for album in albums}
    for r in releases:
        folder = (links or {}).get(r["id"])
        if r["id"] not in used and folder in folders and folder not in found:
            found[folder] = (r, LINK)
            used.add(r["id"])
    by_name = {}
    for album in albums:
        for artist in album_artists(album):
            by_name.setdefault((artist, title_key(album.title)), []).append(album)
    for r in releases:
        for artist in release_artists(r):
            for album in by_name.get((artist, title_key(r["title"])), ()):
                if album.folder not in found:
                    found[album.folder] = (r, NAME)
                used.add(r["id"])
    by_artist, by_title = {}, {}
    for album in albums:
        if album.folder not in found:
            for artist in album_artists(album):
                by_artist.setdefault(loose(artist), []).append(album)
            by_title.setdefault(loose(album.title), []).append(album)
    doubtful, absent = [], []
    for r in releases:
        if r["id"] in used:
            continue
        artists, title = {loose(k) for k in release_artists(r)}, loose(r["title"])
        near = [a for k in artists for a in by_artist.get(k, ()) if close(loose(a.title), title)]
        near += [a for a in by_title.get(title, ())
                 if any(close(loose(k), artist) for k in album_artists(a) for artist in artists)]
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
