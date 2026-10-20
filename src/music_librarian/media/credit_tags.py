"""The credit fields of a file (album artist, artist, composer) in each format."""
from mutagen import File as MFile
from mutagen.flac import FLAC
from mutagen.id3 import TCOM, TPE1, TPE2
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4

KEYS = {
    MP4: {"albumartist": ("aART",), "artist": ("\xa9ART",), "composer": ("\xa9wrt",)},
    MP3: {"albumartist": ("TPE2",), "artist": ("TPE1",), "composer": ("TCOM",)},
    FLAC: {"albumartist": ("albumartist", "album artist"), "artist": ("artist",),
           "composer": ("composer",)},
}
FRAMES = {"TPE1": TPE1, "TPE2": TPE2, "TCOM": TCOM}


def _open(path):
    f = MFile(path)
    kind = next((k for k in KEYS if isinstance(f, k)), None)
    return f, kind


def read(path):
    """{field: [values]} for albumartist, artist and composer; None for a file
    without tags or in a format not handled."""
    f, kind = _open(path)
    if kind is None or f.tags is None:
        return None
    out = {}
    for field, names in KEYS[kind].items():
        values = []
        for name in names:
            if name in f.tags:
                values += f.tags[name].text if kind is MP3 else f.tags[name]
        out[field] = [str(v) for v in values if str(v).strip()]
    return out


def write(path, field, value):
    f, kind = _open(path)
    name = KEYS[kind][field][0]
    if kind is MP3:
        f.tags.setall(name, [FRAMES[name](encoding=3, text=[value])])
    else:
        f.tags[name] = [value]
    f.save()
