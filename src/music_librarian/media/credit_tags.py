"""The credit fields of a file (album artist, artist, composer, and the MusicBrainz ids of
the first two), as split and dedupe read and rewrite them; repeats rewrites any field of fields."""
from music_librarian.core.lock import STOP
from music_librarian.core.log import count
from music_librarian.media import fields

CREDITS = ("albumartist", "artist", "composer", "albumartist_mbid", "artist_mbid")


def read(path):
    """{field: [values]} for each field of CREDITS; None for a format not handled."""
    try:
        values = fields.read(path)
    except fields.FieldError:
        return None
    return {name: values[name] for name in CREDITS}


def write(path, field, value):
    fields.write(path, field, [value])


def from_snapshot(snap):
    """The fields of read() taken from a metadata cache snapshot; None for an unreadable file."""
    if snap is None:
        return None
    easy = {k.lower(): v for k, v in snap["tags"].items()}
    values = lambda *names: [v for n in names for v in easy.get(n, []) if v.strip()]
    return {"albumartist": values("albumartist", "album artist"), "artist": values("artist"),
            "composer": values("composer"), "albumartist_mbid": values("musicbrainz_albumartistid"),
            "artist_mbid": values("musicbrainz_artistid")}


def rewrite_tags(writes, journal, log, stats):
    """Write each (path, field, old, new) and journal it; True if a stop request cut it short."""
    for i, (p, field, old, new) in enumerate(writes):
        if STOP.is_set():
            log(f"STOP     requested: {count(len(writes) - i, 'tag')} not rewritten, left for the next pass")
            return True
        try:
            write(p, field, new)
        except Exception as e:
            log(f"ERROR    ({e})  {p}")
            stats["errors"] += 1
            continue
        journal.tag(p, field, old, new)
        stats["rewritten"] += 1
    return False
