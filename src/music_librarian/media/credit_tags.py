"""The credit fields of a file (album artist, artist, composer, and the MusicBrainz ids of
the first two), as split and dedupe read and rewrite them."""
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
