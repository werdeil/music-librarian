"""The credit fields of a file (album artist, artist, composer, and the MusicBrainz ids of
the first two) in each format."""
from mutagen import File as MFile
from mutagen.flac import FLAC
from mutagen.id3 import TCOM, TPE1, TPE2
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4

MB = "----:com.apple.iTunes:MusicBrainz "
KEYS = {
    MP4: {"albumartist": ("aART",), "artist": ("\xa9ART",), "composer": ("\xa9wrt",),
          "albumartist_mbid": (MB + "Album Artist Id",), "artist_mbid": (MB + "Artist Id",)},
    MP3: {"albumartist": ("TPE2",), "artist": ("TPE1",), "composer": ("TCOM",),
          "albumartist_mbid": ("TXXX:MusicBrainz Album Artist Id",),
          "artist_mbid": ("TXXX:MusicBrainz Artist Id",)},
    FLAC: {"albumartist": ("albumartist", "album artist"), "artist": ("artist",),
           "composer": ("composer",), "albumartist_mbid": ("musicbrainz_albumartistid",),
           "artist_mbid": ("musicbrainz_artistid",)},
}
FRAMES = {"TPE1": TPE1, "TPE2": TPE2, "TCOM": TCOM}


def _open(path):
    f = MFile(path)
    kind = next((k for k in KEYS if isinstance(f, k)), None)
    return f, kind


def read(path):
    """{field: [values]} for each field of KEYS; None for a file without tags or in a
    format not handled."""
    f, kind = _open(path)
    if kind is None or f.tags is None:
        return None
    out = {}
    for field, names in KEYS[kind].items():
        values = []
        for name in names:
            if name in f.tags:
                values += f.tags[name].text if kind is MP3 else f.tags[name]
        values = [v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v) for v in values]
        out[field] = [v for v in values if v.strip()]
    return out


def write(path, field, value):
    f, kind = _open(path)
    name = KEYS[kind][field][0]
    if kind is MP3:
        f.tags.setall(name, [FRAMES[name](encoding=3, text=[value])])
    else:
        f.tags[name] = [value]
    f.save()


def from_snapshot(snap):
    """The fields of read() taken from a metadata cache snapshot; None for an unreadable file."""
    if snap is None:
        return None
    easy = {k.lower(): v for k, v in snap["tags"].items()}
    values = lambda *names: [v for n in names for v in easy.get(n, []) if v.strip()]
    return {"albumartist": values("albumartist", "album artist"), "artist": values("artist"),
            "composer": values("composer"), "albumartist_mbid": values("musicbrainz_albumartistid"),
            "artist_mbid": values("musicbrainz_artistid")}
