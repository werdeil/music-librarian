"""The tag fields the library reads and rewrites, in each format it holds (MP4, MP3, FLAC).

read() gives {field: [values]}, numbers as text ("3"); write() replaces one field's values,
[] removing it. A track or disc number and its total share one tag in MP4 (trkn, disk) and
MP3 (TRCK, TPOS "3/12"): writing one keeps the other.
"""
from mutagen import File as MFile
from mutagen.flac import FLAC
from mutagen.id3 import TXXX, Frames
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4FreeForm

FIELDS = ("title", "artist", "albumartist", "album", "track", "tracktotal", "disc", "disctotal",
          "year", "genre", "composer", "compilation", "albumartist_mbid", "artist_mbid")
NUMBERS = ("track", "tracktotal", "disc", "disctotal")
PAIRS = {"track": ("track", 0), "tracktotal": ("track", 1), "disc": ("disc", 0), "disctotal": ("disc", 1)}

MB = "----:com.apple.iTunes:MusicBrainz "
MP4_KEYS = {"title": "\xa9nam", "artist": "\xa9ART", "albumartist": "aART", "album": "\xa9alb",
            "year": "\xa9day", "genre": "\xa9gen", "composer": "\xa9wrt",
            "albumartist_mbid": MB + "Album Artist Id", "artist_mbid": MB + "Artist Id"}
MP4_PAIRS = {"track": "trkn", "disc": "disk"}
MP3_KEYS = {"title": "TIT2", "artist": "TPE1", "albumartist": "TPE2", "album": "TALB",
            "year": "TDRC", "genre": "TCON", "composer": "TCOM", "compilation": "TCMP",
            "albumartist_mbid": "TXXX:MusicBrainz Album Artist Id",
            "artist_mbid": "TXXX:MusicBrainz Artist Id"}
MP3_PAIRS = {"track": "TRCK", "disc": "TPOS"}
# the first name is the one written; the others are read, and removed on a write
FLAC_KEYS = {"title": ("title",), "artist": ("artist",), "albumartist": ("albumartist", "album artist"),
             "album": ("album",), "track": ("tracknumber",), "tracktotal": ("tracktotal", "totaltracks"),
             "disc": ("discnumber",), "disctotal": ("disctotal", "totaldiscs"), "year": ("date", "year"),
             "genre": ("genre",), "composer": ("composer",), "compilation": ("compilation",),
             "albumartist_mbid": ("musicbrainz_albumartistid",),
             "artist_mbid": ("musicbrainz_artistid",)}


class FieldError(Exception):
    """A format the fields are not handled in, or a value that does not fit the field."""


def _text(values):
    out = [v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v) for v in values]
    return [v for v in (s.strip() for s in out) if v]


def _number(value):
    return [str(value)] if value else []


def _split_pair(text):
    """'3/12' -> ('3', '12'); '3' -> ('3', '')."""
    first, _, second = (text or "").partition("/")
    return first.strip(), second.strip()


def _open(path):
    f = MFile(path)
    if not isinstance(f, (MP4, MP3, FLAC)):
        raise FieldError("format not handled")
    return f


def read(path):
    """{field: [values]} for every field of FIELDS. Raises FieldError for a format not handled."""
    f = _open(path)
    t = f.tags
    if t is None:
        return {name: [] for name in FIELDS}
    if isinstance(f, MP4):
        out = {name: _text(t.get(key, [])) for name, key in MP4_KEYS.items()}
        for name, (pair, index) in PAIRS.items():
            out[name] = _number((t.get(MP4_PAIRS[pair]) or [(0, 0)])[0][index])
        cpil = t.get("cpil")
        out["compilation"] = ["1"] if (cpil[0] if isinstance(cpil, list) and cpil else cpil) else []
    elif isinstance(f, MP3):
        out = {}
        for name, key in MP3_KEYS.items():
            frame = t.get(key)
            values = [] if frame is None else frame.genres if key == "TCON" else frame.text
            out[name] = _text(values)
        out["compilation"] = ["1"] if out["compilation"] == ["1"] else []
        for name, (pair, index) in PAIRS.items():
            frame = t.get(MP3_PAIRS[pair])
            out[name] = _number(_split_pair(str(frame.text[0]) if frame else "")[index])
    else:
        out = {name: _text(v for key in keys for v in t.get(key, [])) for name, keys in FLAC_KEYS.items()}
        for name, total in (("track", "tracktotal"), ("disc", "disctotal")):
            number, embedded = _split_pair(out[name][0] if out[name] else "")
            out[name] = _number(number)
            out[total] = out[total] or _number(embedded)
        out["compilation"] = ["1"] if out["compilation"] == ["1"] else []
    return out


def check(field, values):
    """The values as they are written: a number field takes one integer of 1 or more, the
    compilation flag ["1"] or []. Raises FieldError."""
    if field not in FIELDS:
        raise FieldError(f"unknown field {field!r}")
    values = [v.strip() for v in values if v.strip()]
    if field in NUMBERS:
        if len(values) > 1 or (values and not (values[0].isdigit() and int(values[0]) >= 1)):
            raise FieldError(f"{field} takes a number of 1 or more: {values[0]!r}")
        return [str(int(values[0]))] if values else []
    if field == "compilation":
        flag = values[0].lower() if values else ""
        if flag not in ("", "0", "1", "yes", "no", "true", "false"):
            raise FieldError(f"compilation takes 1 or 0: {values[0]!r}")
        return ["1"] if flag in ("1", "yes", "true") else []
    return values


def write(path, field, values):
    """Replace the values of `field` in the file at `path`; [] removes it. Raises FieldError."""
    values = check(field, values)
    f = _open(path)
    if f.tags is None:
        f.add_tags()
    t = f.tags
    if isinstance(f, MP4):
        _write_mp4(t, field, values)
    elif isinstance(f, MP3):
        _write_mp3(t, field, values)
    else:
        _write_flac(t, field, values)
    f.save()


def _write_mp4(t, field, values):
    if field in PAIRS:
        pair, index = PAIRS[field]
        key = MP4_PAIRS[pair]
        current = list((t.get(key) or [(0, 0)])[0])
        current[index] = int(values[0]) if values else 0
        if any(current):
            t[key] = [tuple(current)]
        else:
            t.pop(key, None)
        return
    if field == "compilation":
        if values:
            t["cpil"] = True
        else:
            t.pop("cpil", None)
        return
    key = MP4_KEYS[field]
    if field == "genre":
        t.pop("gnre", None)
    if not values:
        t.pop(key, None)
    elif key.startswith("----:"):
        t[key] = [MP4FreeForm(v.encode()) for v in values]
    else:
        t[key] = values


def _write_mp3(t, field, values):
    if field in PAIRS:
        pair, index = PAIRS[field]
        key = MP3_PAIRS[pair]
        frame = t.get(key)
        current = list(_split_pair(str(frame.text[0]) if frame else ""))
        current[index] = values[0] if values else ""
        t.delall(key)
        if current[0] or current[1]:
            text = f"{current[0]}/{current[1]}" if current[1] else current[0]
            t.add(Frames[key](encoding=3, text=[text]))
        return
    key = MP3_KEYS[field]
    t.delall(key)
    if not values:
        return
    if key.startswith("TXXX:"):
        t.add(TXXX(encoding=3, desc=key[len("TXXX:"):], text=values))
    else:
        t.add(Frames[key](encoding=3, text=values))


def _write_flac(t, field, values):
    if field in PAIRS:
        number_key = FLAC_KEYS[PAIRS[field][0]][0]
        total_keys = FLAC_KEYS[f"{PAIRS[field][0]}total"]
        number, embedded = _split_pair((t.get(number_key) or [""])[0])
        if embedded:   # a "3/12" total moves to its own tag, so the two never disagree
            if not any(t.get(k) for k in total_keys):
                t[total_keys[0]] = [embedded]
            t[number_key] = [number]
    keys = FLAC_KEYS[field]
    for key in keys:
        if key in t:
            del t[key]
    if values:
        t[keys[0]] = values
